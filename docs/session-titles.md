# Standard session titles

Native publishes the accepted conversation name with the standard ACP notification:

```json
{"method":"session/update","params":{"sessionId":"native-session-id","update":{"sessionUpdate":"session_info_update","title":"Conversation name"}}}
```

This notification requires no Amplifier extension negotiation. Automatic naming
may finish after the prompt response; the same open ACP connection still receives
the title. A successful `session/new`, `session/load`, or `session/resume` also
publishes an existing name after its response, when the client knows the session
identity. An absent or malformed name produces no invented title.

The naming event invalidates presentation only. Native rereads its existing
canonical naming metadata before publishing, so a delayed generated-name event
cannot replace a newer manual name. This path neither generates a name nor writes
metadata, history, or a receipt. Existing naming CAS and exact command outcomes
remain unchanged. Negotiated `_amplifier/event` naming notifications remain
available for extension consumers; hosts should use the standard title update
rather than run a second title projection from that notification.
