from setuptools import setup, find_packages

setup(
    name="multask",
    version="0.4.0",
    packages=find_packages(),
    install_requires=[
        "aiohttp",
        "tqdm"
    ],
    extras_require={
        "pdf": ["pdfplumber", "pdfminer.six"],
    },
    description="Modern Python package for concurrent task execution with intelligent error handling.",
    long_description=open('README.md').read(),
    long_description_content_type="text/markdown",
    author="Ludwig",
    author_email="yuzeliu@gmail.com",
    url=None,
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)