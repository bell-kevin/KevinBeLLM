# SPDX-License-Identifier: AGPL-3.0-or-later
FROM ghcr.io/searxng/searxng:2026.7.28-c01178d03@sha256:80622959f0f3512e6623d6bdbcea9f13c8d22c8d9715c498d0ae2be1c8535930

# The upstream image contains h2 4.4.0, affected by CVE-2026-71554. Keep the
# exact reviewed SearXNG image and overlay only the fixed, hash-verified wheel.
ARG H2_WHEEL_URL=https://files.pythonhosted.org/packages/7e/22/e85faf23bd72a92d1921e37d674ca56eb298a3c8be31fdecef0ff2b3aaac/h2-4.4.1-py3-none-any.whl
ARG H2_WHEEL_SHA256=0e25f1462b23c9cb82d9eb02e28bc706dac2a68cb457c6a0d74d63c8a2a5d0e6
RUN wget -q -O /tmp/h2.whl "${H2_WHEEL_URL}" \
    && test "$(sha256sum /tmp/h2.whl | awk '{print $1}')" = "${H2_WHEEL_SHA256}" \
    && /usr/local/searxng/.venv/bin/python -c "import pathlib, shutil, zipfile; root=pathlib.Path('/usr/local/searxng/.venv/lib/python3.14/site-packages'); [shutil.rmtree(path) for path in (root/'h2', *root.glob('h2-*.dist-info')) if path.exists()]; zipfile.ZipFile('/tmp/h2.whl').extractall(root)" \
    && /usr/local/searxng/.venv/bin/python -c "import h2; assert h2.__version__ == '4.4.1'" \
    && rm /tmp/h2.whl
