#!/bin/sh
# Optional HTTPS in the web container (docker compose on one host): when a certificate is mounted at
# /etc/nginx/certs/tls.crt + tls.key, serve HTTPS on 8443 and redirect plain HTTP to it. Without certs
# (or behind a Kubernetes ingress that terminates TLS) the container serves plain HTTP on 8080 only.
set -eu
out=/tmp/infra-ops-tls.conf
if [ -r /etc/nginx/certs/tls.crt ] && [ -r /etc/nginx/certs/tls.key ]; then
  cat > "$out" <<'CONF'
listen 8443 ssl;
http2 on;
ssl_certificate /etc/nginx/certs/tls.crt;
ssl_certificate_key /etc/nginx/certs/tls.key;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_session_cache shared:infraops_tls:10m;
if ($scheme = http) {
    return 301 https://$host$request_uri;
}
CONF
  echo "infra-ops: HTTPS enabled on 8443, HTTP on 8080 redirects to it"
else
  : > "$out"
  echo "infra-ops: no certificate in /etc/nginx/certs - serving plain HTTP on 8080"
fi
