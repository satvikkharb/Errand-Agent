// Mock agent. The real one lives in the backend (POST /chat); keep this signature and swap the body.

export type Proposal = {
  id: string;
  trade: "Plumber" | "AC technician" | "Electrician";
  provider: string;
  rating: number;
  jobs: number;
  when: string;
  price: number;
  priceNote: string;
};

export type Message =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "agent"; text: string; proposal?: Proposal };

const uid = () => Math.random().toString(36).slice(2, 9);

const PROVIDERS: Record<string, Omit<Proposal, "id" | "when">> = {
  plumber: {
    trade: "Plumber",
    provider: "Ravi Menon",
    rating: 4.8,
    jobs: 212,
    price: 45,
    priceNote: "call-out, parts extra",
  },
  ac: {
    trade: "AC technician",
    provider: "Neha Rao, CoolCare",
    rating: 4.9,
    jobs: 340,
    price: 60,
    priceNote: "full service",
  },
  electrician: {
    trade: "Electrician",
    provider: "Imran Sheikh",
    rating: 4.7,
    jobs: 158,
    price: 40,
    priceNote: "call-out, parts extra",
  },
};

const SLOTS = [
  "Tomorrow, 10:00 to 12:00",
  "Thursday, 14:00 to 16:00",
  "Friday, 09:00 to 11:00",
  "Saturday, 11:00 to 13:00",
];

function detect(text: string): keyof typeof PROVIDERS | null {
  const t = text.toLowerCase();
  if (/(plumb|tap|leak|pipe|drain|sink|toilet|flush|geyser)/.test(t)) return "plumber";
  if (/(\bac\b|air.?con|cooling|compressor|filter|gas refill|split)/.test(t)) return "ac";
  if (/(electr|socket|switch|fuse|wiring|light|bulb|fan|mcb|spark)/.test(t)) return "electrician";
  return null;
}

const OPENERS: Record<string, string> = {
  plumber: "Drips are the worst. Ravi has fixed a lot of these and can come soon.",
  ac: "Sounds like it needs a proper service. Neha's team is great with split units.",
  electrician: "Let's not leave that one waiting. Imran is nearby and can take a look.",
};

export function propose(kind: keyof typeof PROVIDERS, slotIndex = 0): Message {
  const base = PROVIDERS[kind];
  return {
    id: uid(),
    role: "agent",
    text: "",
    proposal: { ...base, id: uid(), when: SLOTS[slotIndex % SLOTS.length] },
  };
}

/** Pretends to think for a moment, then answers with one or two messages. */
export async function askAgent(text: string, history: Message[]): Promise<Message[]> {
  await new Promise((r) => setTimeout(r, 900 + Math.random() * 700));

  const lastProposal = history
    .toReversed()
    .flatMap((m) => (m.role === "agent" && m.proposal ? [m.proposal] : []))[0];

  if (/(different|another|later|other time|reschedule|not quite)/i.test(text) && lastProposal) {
    const next = SLOTS.indexOf(lastProposal.when) + 1;
    const kind = (Object.keys(PROVIDERS) as (keyof typeof PROVIDERS)[]).find(
      (k) => PROVIDERS[k].trade === lastProposal.trade,
    )!;
    return [
      { id: uid(), role: "agent", text: "No problem, here's another slot." },
      propose(kind, next),
    ];
  }

  const kind = detect(text);
  if (kind) {
    return [
      { id: uid(), role: "agent", text: OPENERS[kind] },
      propose(kind, 0),
      {
        id: uid(),
        role: "agent",
        text: "Nothing is booked until you approve it.",
      },
    ];
  }

  if (/^(hi|hey|hello|yo)\b/i.test(text.trim())) {
    return [
      {
        id: uid(),
        role: "agent",
        text: "Hey! Tell me what's wrong at home and I'll find someone to fix it.",
      },
    ];
  }

  return [
    {
      id: uid(),
      role: "agent",
      text: "I can book plumbers, AC servicing and electricians. What's going on, and where in the house?",
    },
  ];
}
