# Pokemon Card Purchase Bot

A Target-focused, retailer-extensible inventory monitor and purchase orchestrator
for Pokemon cards.
It is intentionally safe by default: the included retailer is a local mock and all
checkout attempts are dry runs.

## Design

```text
retailer adapter -> normalized offers -> rule engine -> SQLite claim -> checkout
                                              |              |
                                      price/product rules   audit log
```

The core is separated from retailer-specific code because every store has different
catalog, cart, authentication, and checkout behavior.

- **Retailer adapter:** reads stock and returns normalized offers.
- **Rule engine:** matches configured products, price ceilings, quantities, and the
  daily spending cap.
- **SQLite store:** prevents duplicate purchases and records every attempt.
- **Checkout adapter:** performs a dry run today; a real implementation must use a
  retailer-supported API or normal browser flow and respect its terms, rate limits,
  queues, CAPTCHAs, and purchase limits.
- **Poller:** runs one scan or a continuous loop with randomized jitter.

## Quick start

Python 3.9+ is the only requirement.

```bash
cp config.example.json config.json
python3 -m pokemon_bot --config config.json --once
python3 -m unittest discover -s tests -v
```

With `new_products_only` enabled, the first scan establishes a catalog baseline and
does not buy existing products. A subsequently observed SKU is eligible for the
configured freshness window. State is stored in `pokemon_bot.db`.

To run continuously:

```bash
python3 -m pokemon_bot --config config.json
```

Stop with Ctrl-C. Logs are structured JSON so they can later be sent to Discord,
email, or a monitoring service.

## Notifications

Console notifications work by default. Test them with:

```bash
python3 -m pokemon_bot --config config.json --test-notification
```

For Discord, create a webhook for your private channel, keep its URL out of files,
and update `config.json` to use the environment variable:

```json
"notification": {
  "type": "discord",
  "webhook_env": "DISCORD_WEBHOOK_URL"
}
```

Then launch the monitor from the same terminal:

```bash
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
python3 -m pokemon_bot --config config.json --test-notification
python3 -m pokemon_bot --config config.json
```

Discord messages include a clickable product title for handoff to your normal
browser. Never share or commit the webhook URL.

Notifications use a durable SQLite outbox. If Discord or the network is temporarily
unavailable, an unsent alert remains pending and is retried on the next scan.

## Health and background operation

Every scan persists its start/completion time, health, number of offers, matches,
and retailer errors. View the latest state without starting the monitor:

```bash
python3 -m pokemon_bot --config config.json --status
```

Generate a macOS LaunchAgent definition with absolute paths:

```bash
python3 -m pokemon_bot --config config.json \
  --generate-launch-agent data/com.pokemon-card-target.monitor.plist
```

Generation does not install or start the service. Review the plist first. Discord
secrets are deliberately not written into it; notification credentials must be
provided securely to the background process before installation.

## Authorized Target feed intake

The monitor can ingest a product export supplied by an approved Target integration.
It does not fetch the Target site itself. Each record must include a valid 8-digit
TCIN, Target product URL, title, price in cents, stock status, TCG category, seller,
fulfillment methods, and purchase limit.

```bash
# Establish an empty baseline once before accepting live candidates.
python3 -m pokemon_bot --config config.json --once

python3 -m pokemon_bot --config config.json \
  --ingest-target fixtures/target_ingest.example.json
python3 -m pokemon_bot --config config.json --once
```

Imports are merged atomically by TCIN, so title and availability changes update the
same product. Invalid records are rejected into a JSON Lines quarantine file beside
the configured feed for inspection. An import that contains rejected records exits
with status 2. Do not use the example record as a real product; it exists only to
exercise the pipeline.

## Roadmap

1. Pick one retailer and confirm that automated access and checkout are permitted.
2. Implement its `Retailer` interface using official APIs where available.
3. Add secrets through environment variables or a secret manager—never config files.
4. Add notifications and a human-confirmation checkout mode.
5. Run as a supervised service only after sandbox and low-value end-to-end tests.

## Target integration status

Target's current Terms & Conditions permit checkout agents only when the agent is
approved by both the customer and Target. Unapproved buying agents, automated site
navigation, and scraping are prohibited. The `target_feed` adapter therefore reads
only a local JSON export from an approved source; it does not call private Target
endpoints or automate Target.com.

Target offers an approved shopping experience through its app in ChatGPT. Until an
approved programmatic connection is available to this project, matched products are
sent as clickable alerts for checkout through Target's normal or approved flow.

Target offers are keyed by TCIN and can be restricted to items sold by Target,
specific fulfillment methods, the displayed purchase limit, and a maximum price.

## Pokémon Center status

The U.S. Pokémon Center Terms of Use currently prohibit applications that interact
with the service without prior written consent and prohibit robot/data-extraction
methods. The included project therefore does not scrape, automate checkout, bypass
the virtual queue, or attempt to evade bot controls. A direct adapter should only be
added after written permission or an official supported API becomes available.

The compliant workflow is to use official announcements/email, normalize candidate
products locally, and notify the user for manual purchase in their normal browser.

## Safety defaults

- Checkout is dry-run only.
- The first scan is a baseline, so an old catalog is not mistaken for new releases.
- SKU identity survives title and set-name changes.
- Each normalized offer can be claimed once.
- Price, quantity, and daily-spend limits are mandatory.
- Poll intervals include jitter and cannot be set below 10 seconds.
- The example configuration and database are ignored by Git.
