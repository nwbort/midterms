# midterms

Polls the Guardian's US midterms congress tracker
(`zug.guardianapis.com/.../congress-fronts-tracker/all/v1`) and sends an
[ntfy](https://ntfy.sh) push whenever the Senate or House tally changes, either
a race being called or a party's seat count moving.

Example notification:

```
Midterms: seats called
Senate: D 35 (+1), R 31 · 1/35 called (+1)
```

Senate figures include the seats not up this cycle (D 34, R 31 to start), so
the change in brackets is what matters. On the first run it records a baseline
and sends nothing.

The script is plain Python 3 with no dependencies. It sends the last ETag with
each request, so polls where nothing changed come back as `304` with no body.

## Try it locally

```sh
DRY_RUN=1 python3 watch.py            # prints instead of sending
NTFY_TOPIC=my-topic ONCE=1 python3 watch.py
```

| Variable       | Default              | Notes                                  |
|----------------|----------------------|----------------------------------------|
| `NTFY_TOPIC`   | (required)           | Topic to publish to                    |
| `NTFY_SERVER`  | `https://ntfy.sh`    | Self-hosted server, if you have one    |
| `NTFY_TOKEN`   | unset                | Bearer token for a protected topic     |
| `POLL_SECONDS` | `60`                 | Matches the feed's `max-age=60`        |
| `STATE_FILE`   | `./state.json`       | Last seen tally and ETag               |
| `DRY_RUN`      | unset                | `1` prints instead of sending          |
| `ONCE`         | unset                | `1` polls once and exits               |

## Deploy on a droplet (systemd)

```sh
sudo useradd --system --home /opt/midterms --shell /usr/sbin/nologin midterms
sudo git clone https://github.com/nwbort/midterms.git /opt/midterms
sudo cp /opt/midterms/.env.example /opt/midterms/.env
sudo nano /opt/midterms/.env                     # set NTFY_TOPIC
sudo chown -R midterms:midterms /opt/midterms
sudo chmod 600 /opt/midterms/.env

sudo cp /opt/midterms/deploy/midterms-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now midterms-watch
journalctl -u midterms-watch -f                  # watch the logs
```

Subscribe to the same topic in the ntfy app on your phone.

To start from a fresh baseline, stop the service, delete
`/opt/midterms/state.json` and start it again.
