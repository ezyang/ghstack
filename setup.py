import setuptools
import os
import re

with open("README.md", "r") as fh:
    long_description = fh.read()

here = os.path.abspath(os.path.dirname(__file__))

def read(*parts):
    with open(os.path.join(here, *parts), 'r') as fp:
        return fp.read()

def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]",
                              version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")

setuptools.setup(
    name="ghstack",
    version=find_version("ghstack", "__init__.py"),
    author="Edward Z. Yang",
    author_email="ezyang@mit.edu",
    description="Stack diff support for GitHub",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/pypa/ghexport",
    packages=setuptools.find_packages(exclude=("graphql", "graphql.*",)),
    include_package_data=True,
    package_data={
        'ghstack': ['py.typed'],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    scripts=['bin/ghstack'],
    install_requires=[
        'requests',
        'typing_extensions>=3.7.2',  # need Literal
        'dataclasses',
    ],
    # This is not supported by pip 10, which a lot of people have
    # installed (because it's the conda default)
    #
    # entry_points={
    #     'console_scripts': [
    #         'ghstack = ghstack.__main__:main'
    #     ]
    # },
)
