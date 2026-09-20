FROM mambaorg/micromamba:2.3.2 AS python
USER root
RUN micromamba create -y -p /opt/tokenana -c conda-forge python=3.12.14 pip && micromamba clean --all --yes
ENV PATH=/opt/tokenana/bin:$PATH
COPY agents/mini_swe_agent/upstream/ /build/source/
RUN /opt/tokenana/bin/python -m pip install /build/source && /opt/tokenana/bin/python -m pip freeze > /opt/tokenana/installed-packages.txt
RUN ln -s bin/python /opt/tokenana/python && mkdir -p /opt/tokenana/empty
RUN ln -s bin/mini /opt/tokenana/mini
