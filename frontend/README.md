# Errand frontend

Next.js (App Router) + Tailwind v4. Everything is mocked for now; no backend needed.

```
npm install
npm run dev     # http://localhost:3000
```

## Screens

| Route | What it is |
| --- | --- |
| `/login`, `/signup` | Auth, with a live slice of the product beside the form |
| `/chat` | The conversation: starter prompts, typing indicator, approval card for proposed bookings |
| `/plans` | Free vs Plus |
| `/checkout` | Demo card form and order summary (nothing is charged or stored) |

## Wiring the backend later

- `lib/session.ts`: mock auth in localStorage. Replace `signIn` / `signOut` with Supabase auth.
- `lib/agent.ts`: `askAgent(text, history)` is the one seam. Replace its body with `POST /chat`
  (`{ user_id, message }`). The approval card renders any message that carries a `proposal`.
- `app/checkout/page.tsx`: the `setTimeout` in `pay()` is where a real payment processor goes.

## Design notes

Flat surfaces, no gradients or shadows at rest, one hot orange with ink-dark text on it, Bricolage
Grotesque as the only typeface. Tokens live in `app/globals.css`.
