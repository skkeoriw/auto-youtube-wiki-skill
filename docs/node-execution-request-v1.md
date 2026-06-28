# Node Execution Request v1

The bridge creates a structured request for every real Node Run:

```text
raw/node-runs/{node_run_id}/request.json
```

The same path is passed to the skill process through:

```text
YOUTUBE_WIKI_NODE_EXECUTION_REQUEST
```

The request contains:

- runtime and instance identifiers
- node id and node run id
- entry inputs for isolated node runs or first workflow nodes
- upstream handoff source directories
- Edge Handoff Instruction / Node Execution Guide
- standard output directory and manifest path

Skills should avoid hardcoding upstream or downstream nodes. They should read
the request, consume the input manifest or upstream outputs directories, then
write:

```text
raw/node-runs/{node_run_id}/outputs/manifest.json
```

Legacy `outputs/files/manifest.json` is still read for older runs, but new runs
must use the standard `outputs/` directory.
