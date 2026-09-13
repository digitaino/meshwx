# MeshCore signed-token MQTT broker (observer ingress for CoreScope).
#
# Upstream: https://github.com/michaelhart/meshcore-mqtt-broker (MIT)
# Pinned by commit so builds are reproducible. To bump: change UPSTREAM_REF,
# rebuild, and watch the logs for auth regressions before announcing it.
#
# Observers authenticate by signing a token with their own MeshCore Ed25519
# key — there are no credentials to issue. The only accounts defined here are
# the read-only SUBSCRIBER_* ones (CoreScope), set in broker/.env.

FROM node:22-bookworm-slim AS build

ARG UPSTREAM_REPO=https://github.com/michaelhart/meshcore-mqtt-broker.git
ARG UPSTREAM_REF=4c0496b15a0473045d289527f2ffbe39f73271c5

# git to fetch the pinned tree; build-essential + python3 because
# better-sqlite3 (abuse-detection store) compiles from source on arm64.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates python3 make g++ \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git init -q . \
 && git remote add origin "$UPSTREAM_REPO" \
 && git fetch -q --depth 1 origin "$UPSTREAM_REF" \
 && git checkout -q FETCH_HEAD

# Upstream 301-redirects plain HTTP requests (anything that isn't a WebSocket
# upgrade) to the LetsMesh analyzer. Point curious visitors at our own instead.
RUN sed -i "s|https://analyzer.letsmesh.net/|https://scope.digitaino.com/|" src/server.ts \
 && grep -q "scope.digitaino.com" src/server.ts

RUN npm ci --omit=dev --no-audit --no-fund

FROM node:22-bookworm-slim

ENV NODE_ENV=production
WORKDIR /app

COPY --from=build /src/node_modules ./node_modules
COPY --from=build /src/src ./src
COPY --from=build /src/package.json ./package.json
COPY --from=build /src/tsconfig.json ./tsconfig.json

# Abuse-detection sqlite store lives here (ABUSE_PERSISTENCE_PATH).
RUN mkdir -p /data && chown -R node:node /data /app
USER node

EXPOSE 8883
CMD ["npm", "start"]
