import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="ghstack",
    version="0.0.1",
    author="Edward Z. Yang",
    author_email="ezyang@mit.edu",
    description="Stack diff support for GitHub",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/pypa/ghexport",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    entry_points={
        'console_scripts': [
            'ghstack = ghstack.__main__:main'
        ]
    },
)
