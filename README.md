
# Simply Block
[![Docker Image Build](https://github.com/simplyblock-io/sbcli/actions/workflows/docker-image.yml/badge.svg)](https://github.com/simplyblock-io/sbcli/actions/workflows/docker-image.yml)

[![Python Unit Testing](https://github.com/simplyblock-io/sbcli/actions/workflows/python-testing.yml/badge.svg)](https://github.com/simplyblock-io/sbcli/actions/workflows/python-testing.yml)


## Install
Add the package repo from AWS CodeArtifact using [awscli](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)

```bash
aws codeartifact login --tool pip --repository sbcli --domain simplyblock --domain-owner 565979732541 --region eu-west-1
```
Install package
```bash
pip install --extra-index-url https://pypi.org/simple sbcli-dev
```

# Components

## Simply Block Core
Contains core logic and controllers for the simplyblock cluster

## Simply Block CLI
Please see this document
[README.md](../main/simplyblock_cli/README.md)


## Simply Block Web API
Please see this document
[README.md](../main/simplyblock_web/README.md)



### local development

FoundationDB requires a client library (libfdb_c.dylib) for the Python bindings to interact with the database.
Depending on the OS architecture, please install the appropriate version from the official github repo

```
wget https://github.com/apple/foundationdb/releases/download/7.3.3/FoundationDB-7.3.3_arm64.pkg
```

setup the code on a management node and the webApp code can be developed by building the `docker-compose-dev.yml` file.


```
sudo docker compose -f docker-compose-dev.yml up --build -d
```
