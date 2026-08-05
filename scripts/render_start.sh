#!/bin/sh
# MedRelay Render (Phase 9 hosting decision) web-service start script.
#
# This exists because render.yaml's `dockerCommand` field turned out to have
# undocumented, quirky string handling that broke two different attempts at
# a bare `cmd1 && cmd2 && cmd3`-style inline value:
#
#   1. A plain `cmd1 --noinput && cmd2 --noinput && cmd3 && gunicorn ...`
#      string was split into individual whitespace-separated argv tokens
#      with NO shell interpreting `&&` at all — confirmed in production by
#      `manage.py collectstatic: error: unrecognized arguments: && python
#      manage.py migrate && ...`, and reproduced locally byte-for-byte by
#      exec'ing the same argv split directly with no shell involved.
#   2. Wrapping that in `sh -c "cmd1 && cmd2 && cmd3"` then failed
#      differently — `sh: 1: <the entire cmd1 && cmd2 && cmd3 string, with
#      $PORT already expanded>: not found` — meaning something in Render's
#      own dockerCommand handling (env-var substitution and/or tokenization
#      order, exact mechanism not confirmed) caused the whole chain to be
#      treated as a single literal command name rather than shell syntax,
#      even inside an explicit `sh -c` wrapper.
#
# Rather than keep guessing at Render's exact undocumented dockerCommand
# parsing rules, this script sidesteps the problem entirely: `dockerCommand`
# in render.yaml is now just `sh /app/scripts/render_start.sh` — two plain
# words, no quotes, no `&&`, no `$` for Render's layer to mis-tokenize. All
# the real chaining/quoting/variable-expansion logic lives here, in a real
# file, executed by a real `sh` process reading a real script — the one
# case that cannot be ambiguous no matter how render.yaml's dockerCommand
# field is parsed upstream.
set -e

python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seed_full_demo

exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}"
