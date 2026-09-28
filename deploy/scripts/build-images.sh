#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/../.."
tag="${1:?usage: build-images.sh IMAGE_TAG}"
[[ "$tag" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$ ]] || exit 2
revision="${COSMOS_REVISION:-$(git rev-parse HEAD)}"
docker build --label "org.opencontainers.image.revision=$revision" -t "cosmos-backend:$tag" BackEnd
docker build --label "org.opencontainers.image.revision=$revision" -t "cosmos-frontend:$tag" FrontEnd
