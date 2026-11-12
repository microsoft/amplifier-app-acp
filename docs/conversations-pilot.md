# Try native ACP in a separate conversation room

This opt-in setup connects an already installed `amplifier-acp` to
[Conversations](https://github.com/microsoft/amplifier-constellation-conversations).
Conversations owns the room and public messages. The native agent owns its
execution, private working context and native history. It does not switch a
Unified installation or import existing chats.

## Select the installed inputs

Use an explicit ACP executable and Conversations binary from the versions you
intend to try. Record their versions/source revisions with the pilot; a successful
connection probe is not provider, bundle or live-account qualification. The
Conversations 0.12.8 / ACP 0.1.5 combination has been exercised with synthetic
new/resume, viewer loss, room Stop and settled-turn process disposal. Those checks
do not establish live-provider acceptance or graceful native retirement.

Conversations currently requires its supported Linux/PAM setup. Follow its
[installation instructions](https://github.com/microsoft/amplifier-constellation-conversations/blob/main/docs/installation.md)
for the selected release's prerequisites, local account authentication and HTTPS.
This example installs neither application and requires no private source fetch
from a public Unified install.

Choose a new pilot folder and an existing workspace you own. Run these commands
from this repository's root, replacing every absolute path first:

```sh
set -eu
NATIVE_PILOT_ROOT=/absolute/path/to/new-native-room-pilot
NATIVE_PILOT_WORKSPACE=/absolute/path/to/owned/workspace
NATIVE_PILOT_ACP=/absolute/path/to/selected-environment/bin/amplifier-acp
NATIVE_PILOT_CONVERSATIONS=/absolute/path/to/selected/agent-conversations
test -x "$NATIVE_PILOT_ACP"
test -x "$NATIVE_PILOT_CONVERSATIONS"
test -d "$NATIVE_PILOT_WORKSPACE"
mkdir -m 700 "$NATIVE_PILOT_ROOT"
install -d -m 700 "$NATIVE_PILOT_ROOT/native" "$NATIVE_PILOT_ROOT/native-app" "$NATIVE_PILOT_ROOT/cache"
install -m 600 examples/conversations/agent.json "$NATIVE_PILOT_ROOT/agent.json"
```

The `mkdir` refuses an existing folder. Edit the copied JSON to use the actual
pilot paths and your explicitly selected bundle. JSON paths are literal; shell
variables are not expanded. Keep `home`, `appHome` and `registryHome` separate
from the Conversations folder and other installations. Configure any provider
credentials privately in that native home using the provider's normal setup;
do not put credentials in this example, command arguments or chat messages.
[Launcher module selection](launcher-module-sources.md) describes explicit source
overrides when your selected bundle needs them. Keep its existing tool and
permission choices; connecting a room grants no additional authority.

## Initialize, check the connection and open the room

```sh
"$NATIVE_PILOT_CONVERSATIONS" --home "$NATIVE_PILOT_ROOT/conversations" init \
  --agent-command "$NATIVE_PILOT_ACP" \
  --agent-arg=--config --agent-arg "$NATIVE_PILOT_ROOT/agent.json" \
  --agent-cwd "$NATIVE_PILOT_WORKSPACE" --agent-label "Native pilot" \
  --owner "$USER" --bind 127.0.0.1 --port 0
"$NATIVE_PILOT_CONVERSATIONS" --home "$NATIVE_PILOT_ROOT/conversations" probe
"$NATIVE_PILOT_CONVERSATIONS" --home "$NATIVE_PILOT_ROOT/conversations" serve
```

`probe` negotiates ACP without creating a native session or calling a model.
Keep the foreground `serve` process open. In another terminal, use
`/absolute/path/to/selected/agent-conversations --home /absolute/path/to/new-native-room-pilot/conversations status`.
This uses the same selected binary. `status` reports the last recorded listener;
confirm `serve` is still running. Trust this installation's generated CA as
described in Conversations' installation guide,
or configure an explicitly selected trusted certificate before serving it.

For a remote installation, forward its actual loopback port through your normal
SSH connection and open the HTTPS address at the forwarded port. A preview
handoff should include the exact SSH command, browser URL and certificate
instructions for that installation. Do not expose a control token in a URL.

Sign in as the configured local owner and create a new room. A message can use
the configured real provider; send one only when you intend that work. Existing
rooms resume their saved native binding on the next explicit message. Closing
the browser does not stop accepted work. Use the room's **Stop** action and
existing delivery-recovery controls when needed; do not resend uncertain work
as a new conversation to bypass its outcome.

## Optional context and additional participants

Use Conversations' existing named ACP connections and participant controls for
additional agents. Give each independently configured native participant its own
config, native homes and intended workspace. Room membership and explicit
sharing remain owned by Conversations.

This direct ACP setup receives ordinary prompts and configured MCP attachments;
it does not automatically install Agent Instance's context registry. For that
composition, follow Conversations'
[agent continuity setup](https://github.com/microsoft/amplifier-constellation-conversations/blob/main/docs/installation.md#connect-the-agents-continuity-service)
and the selected Instance/connector's existing MCP configuration. Reuse its
per-agent source credentials and experience-owned context API instead of copying
room transcripts into another store. A context-read credential is not permission
to send messages or administer the experience. Native agent-owned tools keep
their own policy; optional Conversations filesystem/terminal callbacks have
their [separate audience and approval rules](https://github.com/microsoft/amplifier-constellation-conversations/blob/main/docs/client-callbacks.md).

## Stop or retire this pilot

Finish active turns or use room **Stop**, inspect any uncertain delivery, then
stop this pilot's foreground `serve` process with Ctrl-C. Keep the pilot folder
to resume its rooms later. Removing a room does not erase the agent's native
history.

To retire the installation, first verify that no process still uses its named
folders and decide whether its saved work needs to be retained. Remove only the
new pilot folder you created above. Keep the separately selected executable,
bundle, external workspace and other installations. In an owned development
DTU, record the exact resources and cleanup in its workspace manifest.
