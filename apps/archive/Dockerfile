FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /archive
COPY apps/archive/requirements.lock.txt /archive/requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt && groupadd --gid 10001 archive && useradd --uid 10001 --gid 10001 --no-create-home archive
COPY apps/archive/academy_archive.py /archive/academy_archive.py
COPY apps/archive/core_delivery.py apps/archive/archive_health.py /archive/
USER 10001:10001
ENTRYPOINT ["python", "/archive/academy_archive.py"]
CMD ["--help"]
