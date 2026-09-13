FROM python:3.12-slim

COPY images/operator/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY sandbox_operator/ /app/sandbox_operator/

# The operator holds cluster credentials; it has no business running as root.
RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin nonroot
USER 65532

ENV PYTHONPATH=/app
CMD ["kopf", "run", "-m", "sandbox_operator.main", "--verbose"]
