# Contributing to AgentKennel

Issues and pull requests are welcome. The code is plain Python and Bash with no build step:
clone, run `pytest`, change, run `pytest` again. Every change comes with a test.

## Running the tests

```bash
python3 -m pip install --user -r requirements-dev.txt   # once
bin/test                    # tests/ and guests/ on 4 parallel workers (pytest -n 4)
bin/test --random-order     # shuffled: shows tests that lean on each other
bin/test -x -k library      # any pytest option
python3 -m pytest -q        # serial, one process
```

Each parallel worker runs in `bin/pytest-worker`'s sandbox (own PID, mount and network
namespaces): a test there cannot see or signal another worker's processes or the live
terminals, reach the live tmux server, or connect to a port outside its worker. Still
write tests as if they ran side by side: files in `tmp_path`, a server in the test's own
process binds port 0 and reads the port back (a guard test checks this), tmux only
with `-L <unique name>`. A random order (`--random-order`) finds tests that lean on an
earlier one (module-level state, a background thread outliving its test, a shared tmux
session sized by another tab).

One run at a time on a machine: the sandbox hides the rest of the machine from a worker,
not the worker from the machine, so a plain serial `pytest` started next to a parallel
run sees its fake `claude` processes.

## How contributions are licensed

AgentKennel is released under the GNU Affero General Public License v3.0 only
(`AGPL-3.0-only`, see [LICENSE](LICENSE)).

By sending a pull request (or any other contribution) you agree that:

1. your contribution is licensed under `AGPL-3.0-only`, like the rest of the project;
2. you also grant the maintainer (yanhs) a perpetual, worldwide, royalty-free right to use,
   change and distribute your contribution under other terms as well, including commercial
   licenses, so the project can be offered to companies that cannot use AGPL code;
3. it is your own work, or you have the right to contribute it on these terms.

You keep the copyright to your contribution.
