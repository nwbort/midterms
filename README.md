# midterms

Polls the Guardian's US midterms data and sends [ntfy](https://ntfy.sh) pushes for:

- **Senate** (`interactive.guim.co.uk/.../details/results_S.json`):
  - any call in a toss-up state, `SENATE_STATES` (default AK, IA, KS, ME, OH, TX)
  - any race anywhere that is called as a flip
  - a withdrawn call for either of the above
- **House** (`zug.guardianapis.com/.../congress-fronts-tracker/all/v1`): once,
  when a party reaches 218 seats.
- **House districts** (`.../details/results_H.json`, rated by the Guardian's
  `battlegrounds.json`):
  - any call in a **Toss Up** district
  - any district (Lean, Likely or Solid) called as a flip
  - a withdrawn call for either of the above
  - Ratings are fetched once at startup and saved in the state file in case
    a later fetch fails.

Example notifications:

```
Senate race called
Michigan: Mike Rogers (R) wins, 51.2% · R FLIP from D

House called for the Democrats
House: D 218, R 190 · 408/435 called

House flip
AZ-06 (Lean D): Jane Roe (R) wins, 50.4% · R FLIP from D
```

On the first run each feed records a baseline and sends nothing.

The script is plain Python 3 with no dependencies. It sends the last ETag with
each request to each feed, so polls where nothing changed come back as `304` with no body.

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
| `SENATE_STATES` | `AK,IA,KS,ME,OH,TX` | Toss-ups: notify on any call         |
| `BATTLEGROUNDS_URL` | Guardian's file | Race ratings (`battlegrounds.json`) |
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
