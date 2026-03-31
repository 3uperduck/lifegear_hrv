ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN pip3 install --no-cache-dir pycryptodome

COPY m8_local_server.py /
COPY run.sh /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
