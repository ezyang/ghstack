#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import datetime
import functools
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

DATETIME_FORMAT = "%Y-%m-%d_%Hh%Mm%Ss"


RE_LOG_DIRNAME = re.compile(
    r"(\d{4}-\d\d-\d\d_\d\dh\d\dm\d\ds)_" r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
)


class Formatter(logging.Formatter):
    redactions: Dict[str, str]

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None):
        super().__init__(fmt, datefmt)
        self.redactions = {}

    # Remove sensitive information from URLs
    def _filter(self, s: str) -> str:
        s = re.sub(r":\/\/(.*?)\@", r"://<USERNAME>:<PASSWORD>@", s)
        for needle, replace in self.redactions.items():
            s = s.replace(needle, replace)
        return s

    def formatMessage(self, record: logging.LogRecord) -> str:
        if record.levelno == logging.INFO or record.levelno == logging.DEBUG:
            # Log INFO/DEBUG without any adornment
            return record.getMessage()
        else:
            # I'm not sure why, but formatMessage doesn't show up
            # even though it's in the typeshed for Python >3
            return super().formatMessage(record)  # type: ignore

    def format(self, record: logging.LogRecord) -> str:
        return self._filter(super().format(record))

    # Redact specific strings; e.g., authorization tokens.  This won't
    # retroactively redact stuff you've already leaked, so make sure
    # you redact things as soon as possible
    def redact(self, needle: str, replace: str = "<REDACTED>") -> None:
        # Don't redact empty strings; this will lead to something
        # that looks like s<REDACTED>t<REDACTED>r<REDACTED>...
        if needle == "":
            return
        self.redactions[needle] = replace


formatter = Formatter(fmt="%(levelname)s: %(message)s", datefmt="")


class HandlerMetrics:
    def __init__(self, name: str) -> None:
        self.name = name
        self.records = 0
        self.bytes = 0
        self.max_record_bytes = 0
        self.emit_seconds = 0.0
        self.format_seconds = 0.0
        self.write_seconds = 0.0
        self.flush_seconds = 0.0

    def record(
        self,
        *,
        record_bytes: int,
        emit_seconds: float,
        format_seconds: float,
        write_seconds: float,
        flush_seconds: float,
    ) -> None:
        self.records += 1
        self.bytes += record_bytes
        self.max_record_bytes = max(self.max_record_bytes, record_bytes)
        self.emit_seconds += emit_seconds
        self.format_seconds += format_seconds
        self.write_seconds += write_seconds
        self.flush_seconds += flush_seconds


def _log_metrics_enabled() -> bool:
    return os.environ.get("GHSTACK_LOG_METRICS", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _encoded_len(stream: Any, s: str) -> int:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    return len(s.encode(encoding, errors="backslashreplace"))


def _emit_with_metrics(
    handler: logging.StreamHandler[Any],
    record: logging.LogRecord,
    metrics: HandlerMetrics,
) -> None:
    emit_start = time.perf_counter()
    try:
        format_start = time.perf_counter()
        msg = handler.format(record)
        format_seconds = time.perf_counter() - format_start

        stream = handler.stream
        output = msg + handler.terminator
        record_bytes = _encoded_len(stream, output)

        write_start = time.perf_counter()
        stream.write(output)
        write_seconds = time.perf_counter() - write_start

        flush_start = time.perf_counter()
        handler.flush()
        flush_seconds = time.perf_counter() - flush_start

        emit_seconds = time.perf_counter() - emit_start
        metrics.record(
            record_bytes=record_bytes,
            emit_seconds=emit_seconds,
            format_seconds=format_seconds,
            write_seconds=write_seconds,
            flush_seconds=flush_seconds,
        )
    except RecursionError:
        raise
    except Exception:
        handler.handleError(record)


class MetricStreamHandler(logging.StreamHandler):  # type: ignore[type-arg]
    def __init__(self, metrics: HandlerMetrics) -> None:
        super().__init__()
        self.metrics = metrics

    def emit(self, record: logging.LogRecord) -> None:
        _emit_with_metrics(self, record, self.metrics)


class MetricFileHandler(logging.FileHandler):
    def __init__(self, filename: str, metrics: HandlerMetrics) -> None:
        super().__init__(filename)
        self.metrics = metrics

    def emit(self, record: logging.LogRecord) -> None:
        _emit_with_metrics(self, record, self.metrics)


def _report_metrics(metrics: List[HandlerMetrics], log_file: str) -> None:
    for metric in metrics:
        sys.stderr.write(
            "[ghstack logging] {name}: records={records} bytes={bytes} "
            "max_record_bytes={max_record_bytes} emit={emit:.1f}ms "
            "format={format:.1f}ms write={write:.1f}ms flush={flush:.1f}ms\n".format(
                name=metric.name,
                records=metric.records,
                bytes=metric.bytes,
                max_record_bytes=metric.max_record_bytes,
                emit=metric.emit_seconds * 1000,
                format=metric.format_seconds * 1000,
                write=metric.write_seconds * 1000,
                flush=metric.flush_seconds * 1000,
            )
        )
    try:
        log_size = os.path.getsize(log_file)
    except OSError:
        return
    sys.stderr.write(
        "[ghstack logging] file_size={} path={}\n".format(log_size, log_file)
    )


@contextlib.contextmanager
def manager(*, debug: bool = False) -> Iterator[None]:
    # TCB code to setup logging.  If a failure starts here we won't
    # be able to save the user in a reasonable way.

    # Logging structure: there is one logger (the root logger)
    # and in processes all events.  There are two handlers:
    # stderr (INFO) and file handler (DEBUG).
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    metrics: List[HandlerMetrics] = []
    log_metrics = _log_metrics_enabled()

    if log_metrics:
        console_metrics = HandlerMetrics("console")
        metrics.append(console_metrics)
        console_handler: logging.StreamHandler[Any] = MetricStreamHandler(
            console_metrics
        )
    else:
        console_handler = logging.StreamHandler()
    if debug:
        console_handler.setLevel(logging.DEBUG)
    else:
        console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    log_file = os.path.join(run_dir(), "ghstack.log")

    if log_metrics:
        file_metrics = HandlerMetrics("file")
        metrics.append(file_metrics)
        file_handler: logging.FileHandler = MetricFileHandler(log_file, file_metrics)
    else:
        file_handler = logging.FileHandler(log_file)
    # TODO: Hypothetically, it is better if we log the timestamp.
    # But I personally feel the timestamps gunk up the log info
    # for not much benefit (since we're not really going to be
    # in the business of debugging performance bugs, for which
    # timestamps would really be helpful.)  Perhaps reconsider
    # at some point based on how useful this information actually is.
    #
    # If you ever switch this, make sure to preserve redaction
    # logic...
    file_handler.setFormatter(formatter)
    # file_handler.setFormatter(logging.Formatter(
    #    fmt="[%(asctime)s] [%(levelname)8s] %(message)s"))
    root_logger.addHandler(file_handler)

    record_argv()

    try:
        # Do logging rotation
        rotate()

        yield

    except Exception as e:
        logging.exception("Fatal exception")
        record_exception(e)
        sys.exit(1)

    finally:
        console_handler.flush()
        file_handler.flush()
        if log_metrics:
            _report_metrics(metrics, log_file)
        root_logger.removeHandler(console_handler)
        root_logger.removeHandler(file_handler)
        console_handler.close()
        file_handler.close()


@functools.lru_cache()
def base_dir() -> str:
    # Don't use shell here as we are not allowed to log yet!
    try:
        meta_dir = subprocess.run(
            ("git", "rev-parse", "--git-dir"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            encoding="utf-8",
        ).stdout.rstrip()
    except subprocess.CalledProcessError:
        meta_dir = os.path.join(
            subprocess.run(
                ("hg", "root"), stdout=subprocess.PIPE, encoding="utf-8", check=True
            ).stdout.rstrip(),
            ".hg",
        )

    base_dir = os.path.join(meta_dir, "ghstack", "log")

    try:
        os.makedirs(base_dir)
    except FileExistsError:
        pass

    return base_dir


# Naughty, "run it once and save" memoizing
@functools.lru_cache()
def run_dir() -> str:
    # NB: respects timezone
    cur_dir = os.path.join(
        base_dir(),
        "{}_{}".format(datetime.datetime.now().strftime(DATETIME_FORMAT), uuid.uuid1()),
    )

    try:
        os.makedirs(cur_dir)
    except FileExistsError:
        pass

    return cur_dir


def record_exception(e: BaseException) -> None:
    with open(os.path.join(run_dir(), "exception"), "w") as f:
        f.write(type(e).__name__)


@functools.lru_cache()
def record_argv() -> None:
    with open(os.path.join(run_dir(), "argv"), "w") as f:
        f.write(subprocess.list2cmdline(sys.argv))


def record_status(status: str) -> None:
    with open(os.path.join(run_dir(), "status"), "w") as f:
        f.write(status)


def rotate() -> None:
    log_base = base_dir()
    old_logs = os.listdir(log_base)
    old_logs.sort(reverse=True)
    for stale_log in old_logs[1000:]:
        # Sanity check that it looks like a log
        assert RE_LOG_DIRNAME.fullmatch(stale_log)
        shutil.rmtree(os.path.join(log_base, stale_log))
