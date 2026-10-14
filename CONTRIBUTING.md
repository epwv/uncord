# Contributing

Bug reports, ideas and pull requests are all welcome. For questions, the [Discord server](https://discord.gg/fubfK8C6Cm) is the quickest place.

## Setting up

```sh
git clone https://github.com/epwv/uncord.git && cd uncord
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m uncord
```

After editing `uncord/_native.c`, rebuild it with `.venv/bin/python setup.py build_ext --inplace`.

## Style

- camelCase names
- few comments, written as `#lowercase and short`
- no docstrings
- config keys stay snake_case so old config files keep working
- the server and DM list stays plain text

Run `pyflakes uncord` before sending a pull request. CI runs it too.
