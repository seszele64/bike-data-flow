from setuptools import find_packages, setup

setup(
    name="wrm_pipeline",
    packages=find_packages(exclude=["wrm_pipeline_tests"]),
    install_requires=[
        "dagster",
        "dagster-cloud"
    ],
    extras_require={"dev": ["dagster-webserver", "pytest"]},
)
