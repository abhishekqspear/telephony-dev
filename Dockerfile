FROM python:3.10.13-slim

ARG TWILIO_ACCOUNT_SID
ARG TWILIO_AUTH_TOKEN
ARG TWILIO_PHONE_NUMBER
ARG PLIVO_AUTH_ID
ARG PLIVO_AUTH_TOKEN
ARG PLIVO_PHONE_NUMBER

ENV TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}
ENV TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN}
ENV TWILIO_PHONE_NUMBER=${TWILIO_PHONE_NUMBER}
ENV PLIVO_AUTH_ID=${PLIVO_AUTH_ID}
ENV PLIVO_AUTH_TOKEN=${PLIVO_AUTH_TOKEN}
ENV PLIVO_PHONE_NUMBER=${PLIVO_PHONE_NUMBER}

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=/app/requirements.txt \
    pip install --no-cache-dir -r requirements.txt
COPY plivo_api_server.py /app/

EXPOSE 8002

CMD ["uvicorn", "plivo_api_server:app", "--host", "0.0.0.0", "--port", "8002"]
