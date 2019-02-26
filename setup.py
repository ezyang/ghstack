import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="ghstack",
    version="0.0.4",
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
    scripts=['bin/ghstack'],
    install_requires=[
        'requests',
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
