#!/bin/bash

VERSION=0.1.0
IMAGE=ghcr.io/edward-rse/fasterwhisper-api

sudo -v

echo "$GHCR_TOKEN" | sudo docker login ghcr.io -u Edward-RSE --password-stdin

sudo docker build -t "$IMAGE:latest" -t "$IMAGE:$VERSION" .
sudo docker push "$IMAGE:latest"
sudo docker push "$IMAGE:$VERSION"

