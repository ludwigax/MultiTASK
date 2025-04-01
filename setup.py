from setuptools import setup, find_packages

setup(
    name="multask",
    version="0.3.1",
    packages=find_packages(),
    install_requires=[
        "aiohttp"
    ],
    description="multask is a Python package for asynchronous task execution.",
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