FROM node:20-bookworm-slim
ENV npm_config_update_notifier=false npm_config_fund=false
RUN useradd --create-home --uid 10001 runner
WORKDIR /workspace
USER runner
ENTRYPOINT ["/bin/sh", "-lc"]
