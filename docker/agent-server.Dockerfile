FROM ghcr.io/openhands/agent-server:1.63.1-python

USER root
RUN python -m pip install --no-cache-dir pytest
USER openhands
