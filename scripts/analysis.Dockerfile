FROM tokenana/controller:pilot-v1
COPY requirements/analysis.txt /tmp/analysis-requirements.txt
RUN /opt/tokenana/bin/python -m pip install -r /tmp/analysis-requirements.txt
ENV MPLCONFIGDIR=/tmp/tokenana-matplotlib
WORKDIR /repo
ENTRYPOINT []
