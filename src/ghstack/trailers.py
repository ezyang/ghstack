#!/usr/bin/env python3

import re
from typing import List, Tuple

# Compile regexes once at module level for better performance
TRAILER_RE = re.compile(r"^([A-Za-z0-9_-]+)(\s*:\s*)(.*)$")
CONTINUATION_RE = re.compile(r"^\s+\S.*$")

# Git-generated trailer prefixes
GIT_GENERATED_PREFIXES = ["Signed-off-by: ", "(cherry picked from commit "]


def parse_message(message: str) -> Tuple[str, str, str]:
    """
    Parse a Git commit message into subject, body, and trailers.

    According to the Git documentation, trailers are:
    - A group of one or more lines that is all trailers, or contains at least one
      Git-generated or user-configured trailer and consists of at least 25% trailers.
    - The group must be preceded by one or more empty (or whitespace-only) lines.
    - The group must either be at the end of the message or be the last non-whitespace
      lines before a line that starts with "---" (the "divider").

    Args:
        message: The commit message to parse.

    Returns:
        A tuple containing:
            - subject: The first line of the message
            - body: The body of the message (may be empty)
            - trailers: The trailer block as a raw string (may be empty)
    """
    if not message:
        return "", "", ""

    # Split into lines and get the subject (first line)
    lines = message.splitlines()
    subject = lines[0] if lines else ""

    if len(lines) <= 1:
        return subject, "", ""

    # Remove subject
    message_lines = lines[1:]

    if not message_lines:
        return subject, "", ""

    # Find where the trailer block starts
    trailer_start = find_trailer_block_start(message_lines)

    if trailer_start == -1:
        # No trailer block found, everything after subject is body
        body = "\n".join(message_lines).strip()
        return subject, body, ""

    # Body is everything between subject and trailers (with empty lines trimmed)
    body = "\n".join(message_lines[:trailer_start]).strip()

    # Keep trailers as a raw string
    trailers = "\n".join(message_lines[trailer_start:]).strip()

    return subject, body, trailers


def find_trailer_block_start(lines: List[str]) -> int:
    """
    Find the start index of the trailer block in a list of lines.

    Args:
        lines: List of message lines (without subject and divider).

    Returns:
        Index of the first line of the trailer block, or -1 if no trailer block is found.
    """
    # Remove trailing empty lines
    trimmed_lines = list(reversed([line for line in reversed(lines) if line.strip()]))

    if not trimmed_lines:
        return -1

    # Find the last non-empty block
    block_indices = [-1] + [i for i, line in enumerate(lines) if not line.strip()]

    # Try blocks from last to first
    for i in range(len(block_indices) - 1, -1, -1):
        start_idx = block_indices[i] + 1
        # If we're at the beginning or checking the whole message
        if i == 0 or start_idx == 0:
            # Check if the whole remaining content is a trailer block
            if is_trailer_block(lines[start_idx:]):
                return start_idx
            # No more blocks to check
            return -1

        # Check if the block after this blank line is a trailer block
        end_idx = block_indices[i + 1] if i + 1 < len(block_indices) else len(lines)
        if is_trailer_block(lines[start_idx:end_idx]):
            return start_idx

    return -1


def is_trailer_block(lines: List[str]) -> bool:
    """
    Determine if the given lines form a trailer block.

    A block is a trailer block if:
    1. All lines are trailers, or
    2. At least one Git-generated trailer exists and at least 25% of lines are trailers

    Args:
        lines: List of lines to check.

    Returns:
        True if the lines form a trailer block, False otherwise.
    """
    # Filter out empty lines
    content_lines = [line for line in lines if line.strip()]

    if not content_lines:
        return False

    trailer_lines = 0
    non_trailer_lines = 0
    has_git_generated_trailer = False

    i = 0
    while i < len(content_lines):
        line = content_lines[i]

        # Skip continuation lines (they belong to the previous trailer)
        if CONTINUATION_RE.match(line):
            i += 1
            continue

        # Check if it's a git-generated trailer
        if any(line.startswith(prefix) for prefix in GIT_GENERATED_PREFIXES):
            has_git_generated_trailer = True
            trailer_lines += 1
        elif TRAILER_RE.match(line):
            # Regular trailer
            trailer_lines += 1
        else:
            # Not a trailer line
            non_trailer_lines += 1

        i += 1

    # A block is a trailer block if all lines are trailers OR
    # it has at least one git-generated trailer and >= 25% of lines are trailers
    return (trailer_lines > 0 and non_trailer_lines == 0) or (
        has_git_generated_trailer and trailer_lines * 3 >= non_trailer_lines
    )


def interpret_trailers(message: str, trailers_to_add: List[str]) -> str:
    """
    Add trailers to a commit message, mimicking git interpret-trailers.

    Args:
        message: The commit message to add trailers to
        trailers_to_add: List of trailers to add in the format "Key: Value"

    Returns:
        The commit message with trailers added
    """
    subject, body, existing_trailers = parse_message(message)

    # Create a new list with all trailers (existing + new)
    all_trailers = []
    if existing_trailers:
        all_trailers.append(existing_trailers)

    all_trailers.extend(trailers_to_add)

    # Build the new message
    new_message = subject

    if body:
        new_message += "\n\n" + body

    if all_trailers:
        if body or (not body and existing_trailers):
            new_message += "\n"
        if not existing_trailers:
            new_message += "\n"
        new_message += "\n" + "\n".join(all_trailers)

    return new_message
