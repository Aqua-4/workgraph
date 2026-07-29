```yaml
tags:
  # NOTE: Keep this list limited to non-sensitive historical project aliases only.
  # Do NOT add current client names, repos, or internal identifiers here.
  # Approved placeholders: compass, tenet, atlas.
  Client Delivery:
    repos:
      - compass
      - tenet
      - atlas
    domains:
      - teams.microsoft.com
      - meet.google.com
      - zoom.us
      - webex.com
    keywords:
      - client
      - delivery
      - delivery_work
      - meeting
      - standup
      - huddle
      - review
      - sprint
      - roadmap
      - workstream
      - project
```

instead of this maybe we can add one more key to store the intent

```yaml
tags:
  # NOTE: Keep this list limited to non-sensitive historical project aliases only.
  # Do NOT add current client names, repos, or internal identifiers here.
  # Approved placeholders: compass, tenet, atlas.
  Client Delivery:
    repos:
      - compass
      - tenet
      - atlas
    domains:
      - teams.microsoft.com
      - meet.google.com
      - zoom.us
      - webex.com
    keywords:
      - client
      - delivery
      - delivery_work
      - meeting
      - standup
      - huddle
      - review
      - sprint
      - roadmap
      - workstream
      - project
    intent:
      - Client Delivery
      - Meetings
      - Planning
```
