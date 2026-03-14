ARG BUILD_FROM
FROM $BUILD_FROM

COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
