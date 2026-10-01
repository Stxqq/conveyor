FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY conveyor ./conveyor
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONVEYOR_HOME=/data
RUN useradd --create-home --uid 10001 conveyor \
    && mkdir /data && chown conveyor /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
WORKDIR /app
COPY --chown=conveyor examples ./examples
USER conveyor
VOLUME /data
EXPOSE 5300 8000
CMD ["conveyor", "ui", "--host", "0.0.0.0"]
