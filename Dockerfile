FROM node:20-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    apache2 \
    curl \
    ffmpeg \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN a2enmod proxy proxy_http headers

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

RUN pip3 install --no-cache-dir --break-system-packages \
    psycopg2-binary \
    requests \
    beautifulsoup4 \
    lxml

COPY . /app

COPY apache.conf /etc/apache2/sites-available/000-default.conf
RUN chmod +x /app/entrypoint.sh

EXPOSE 80

CMD ["/app/entrypoint.sh"]