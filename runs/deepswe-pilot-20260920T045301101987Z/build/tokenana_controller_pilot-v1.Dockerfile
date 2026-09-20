FROM docker:27-cli AS dockercli
FROM mambaorg/micromamba:2.3.2 AS python
USER root
RUN micromamba create -y -p /opt/tokenana -c conda-forge python=3.12.14 pip && micromamba clean --all --yes
ENV PATH=/opt/tokenana/bin:$PATH
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN python -m pip install tiktoken jinja2 httpx requests && python -m pip freeze > /opt/tokenana/installed-packages.txt
RUN python -c "import tiktoken; tiktoken.encoding_for_model('gpt-4o')"
ENTRYPOINT []
