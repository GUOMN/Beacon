# Custom local data sources

Beacon supports user-defined local data sources without embedding any private
wire protocol in this repository. Source definitions are stored in the current
user's application-data directory as `custom-sources.json`; they are never
written into the project checkout.

## Adapter boundary

Beacon starts the configured adapter directly as an argument vector (never
through a command shell), connects the configured local endpoint itself, and
writes one UTF-8 JSON line to the adapter's standard input:

```json
{
  "schema": "beacon.custom-source/2",
  "source_id": "local-source-id",
  "adapter_config_path": "/local/path/private-adapter.json"
}
```

After that configuration line, Beacon forwards bytes received from the local
endpoint to the adapter's standard input without interpreting them. TCP
endpoints are restricted to loopback hosts. The adapter owns only private
protocol decoding; it does not open the socket.

For bidirectional protocols, an adapter can ask Beacon to write UTF-8 data to
the connected endpoint by emitting a control line. Binary data can instead be
provided in the `data_base64` field.

```json
{"schema":"beacon.adapter-control/1","action":"send","data":"subscribe\n"}
```

The adapter writes normalized events as JSON Lines to standard output:

```json
{"task_id":"task-1","title":"Local task","state":"running","progress":20,"agent":"worker-a"}
```

Required fields are `task_id` and `state`. Supported states are `running`,
`waiting`, `success`, `warning`, and `failure`. Optional fields are `title`,
`progress`, `occurred_at_ms`, `event_key`, `input_tokens`, `output_tokens`, and
`agent`. When `agent` is provided, Beacon displays the event source as
`<custom source name>-<agent>`; otherwise it uses the custom source name.
Beacon namespaces task/event identifiers so one custom source cannot collide
with another.

Do not add real socket paths, ports, adapter scripts, private configuration, or
protocol examples to this repository. Enter those values only in the installed
application on the machine where the adapter runs. If local development ever
requires files beside the checkout, keep them under `.beacon-local/`, which is
explicitly excluded from Git.
