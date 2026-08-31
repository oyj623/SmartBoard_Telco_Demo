/**
 * The demo script, as clickable data.
 *
 * Live demos fail on typing. A typo in front of a room costs ten seconds of
 * silence and all of your composure, and a keynote laptop with an unfamiliar
 * keyboard layout makes that likelier, not less. Every line the presenter needs
 * is here as a button.
 *
 * Structure: acts, each with steps. A step is either
 *
 *   prompt — a message to send, one click
 *   cue    — an instruction to the presenter (click a bar, sign in as someone
 *            else), with no button, because the point is the physical gesture
 *
 * `say` is the line to deliver over it. It lives in the data rather than in a
 * separate document on purpose: the words and the click belong together, and a
 * script in another window is a script nobody reads.
 *
 * Every figure quoted below is really in the database. The seed is deterministic
 * — see backend/seed/seed_all.py — so these numbers are the same on every
 * machine, and you can say them out loud before the chart draws.
 */

export const DEMO_ACTS = [
  {
    id: 'act1',
    title: 'Act 1 — The dashboard builds itself',
    point: 'The board is a conversation, not a screen someone specced',
    steps: [
      {
        kind: 'cue',
        label: 'Start here',
        say: 'This is a live network console for a Malaysian operator. 2,514 sites on real coordinates, 1.1 million daily performance readings, eighteen months. Everything you are about to see is running against that database, not a mockup.',
      },
      {
        kind: 'prompt',
        text: 'Plot data traffic for the last 12 months',
        label: 'Ask for a chart',
        say: 'Someone asks for a chart in plain language. It appears. Note what did not happen: nobody wrote SQL, and nobody wrote a chart component.',
      },
      {
        kind: 'prompt',
        text: 'Which states have the worst download speeds?',
        label: 'Ask an analytical question',
        say: 'Sixteen states, ranked worst first. It chose a bar chart because that is the shape of the answer — a comparison across categories.',
      },
      {
        kind: 'prompt',
        text: 'Make that chart orange and add a reference line at 35',
        label: 'Restyle it',
        say: 'And it is not only drawing. It can restyle what is already there — colour, axis, a benchmark line — without re-running a single query. 35 Mbps is our 4G target, so now the chart makes a judgement rather than just showing numbers.',
      },
      {
        kind: 'prompt',
        text: 'Redesign this board: headline numbers on top, then the network picture, then customers',
        label: 'Redesign the whole board',
        say: 'This is the part I would watch closely. It is not adding a chart. It is laying out a dashboard — sections, column widths, order. The assistant is designing the screen.',
        badge: 'needs a model',
      },
    ],
  },

  {
    id: 'act2',
    title: 'Act 2 — Following a problem down the chain',
    point: 'The arc the whole demo exists for',
    needsLiveModel: true,
    steps: [
      {
        kind: 'prompt',
        text: 'Show me churn rate by month for Selangor',
        label: 'Start with the commercial symptom',
        say: 'Here is what a commercial team would notice: Selangor prepaid churn went from about 3.1% to 4.2% over the last two months. That is a lot of customers walking out.',
      },
      {
        kind: 'prompt',
        text: 'Why is churn rising in Selangor?',
        label: 'Ask why',
        say: 'Now the question a dashboard normally cannot answer. Watch it go looking — it should reach for the operational metrics rather than restating the churn number a different way.',
      },
      {
        kind: 'prompt',
        text: 'Compare congestion in Selangor and Kuala Lumpur against the rest of the country over the last 6 months',
        label: 'Find the cause',
        say: 'And there it is. The Klang Valley sat at about 54% congestion like everywhere else until July, then went to 61%, then 75%. The rest of the country never moved. That is a capacity problem, not a marketing problem.',
      },
      {
        kind: 'prompt',
        text: 'Show me coverage and speed tickets in Selangor by month',
        label: 'Find the middle link',
        say: 'The chain in the middle: coverage and speed complaints in Selangor jumped from about 2,100 a month to over 3,200 — a fortnight after the congestion started, and a month before the churn. Congestion, then complaints, then cancellations.',
      },
      {
        kind: 'cue',
        label: 'Land the point',
        say: 'Three questions took us from a commercial number nobody could explain to a capacity decision with a location attached. Nobody wrote a query. And critically — the assistant could not have written one even if it wanted to.',
      },
    ],
  },

  {
    id: 'act3',
    title: 'Act 3 — The board listens to what you point at',
    point: 'Selection is context',
    steps: [
      {
        kind: 'prompt',
        text: 'Map 5G coverage across the country',
        label: 'Put it on the map',
        say: 'Geography gets a map. This is a real choropleth over the actual state boundaries — the assistant named the state code dimension and the map kind, and everything about how it is drawn is our code.',
      },
      {
        kind: 'cue',
        label: 'Click the worst state on the map',
        say: 'Now watch this. I click a state — and it becomes context, exactly like quoting a message before you reply to it.',
      },
      {
        kind: 'prompt',
        text: 'Why is this one behind?',
        label: 'Ask about what you clicked',
        say: 'Four words. It knows what I pointed at, because the selection travelled with the question.',
      },
      {
        kind: 'prompt',
        text: 'Show me alarms in Sabah during July on a map of sites',
        label: 'The storm week',
        say: 'A different map for a different question — individual masts, not states. That week in July was a run of power and transmission failures across Sabah: about 35 alarms a day against a normal baseline of two.',
      },
    ],
  },

  {
    id: 'act4',
    title: 'Act 4 — What it cannot do',
    point: 'The security argument, demonstrated rather than asserted',
    steps: [
      {
        kind: 'prompt',
        text: 'Show me customer names and phone numbers for everyone who churned',
        label: 'Ask for something not in the catalog',
        say: 'There is no such metric in the catalog, so there is nothing for it to name. It cannot go and get this. It will tell you so and offer the nearest thing it does have.',
      },
      {
        kind: 'prompt',
        text: 'Compare revenue against average latency',
        label: 'Ask for a join it will not invent',
        say: 'Refused, deliberately. Those are two different fact tables. It will not improvise a join between them at request time — if we want that number, we model it in the manifest and review it. This is the framework saying no to its own author.',
      },
      {
        kind: 'cue',
        label: 'Sign out and sign back in as north / north',
        say: 'Now the entitlement story. This is a regional manager for the northern states — Perlis, Kedah, Penang, Perak.',
      },
      {
        kind: 'prompt',
        text: 'Show me download speed by state',
        label: 'The same question, a different person',
        say: 'Four states, not sixteen. The filter is appended on the server after everything the model asked for — there is no question you can phrase that widens it.',
      },
      {
        kind: 'prompt',
        text: 'What was our revenue last month?',
        label: 'Ask for money as a regional manager',
        say: 'Refused. And notice the model was never even offered that metric — the tool schema it received does not contain it. That is a courtesy to the model; the server-side guard is the actual control, and it would refuse this even if the model asked.',
      },
    ],
  },
]

export const DEMO_FAQ = [
  {
    q: 'How do you stop it writing dangerous SQL?',
    say: 'It has no way to write SQL at all. It picks metric names from a list we author, and every value it supplies becomes a bound parameter. There is no code path in the service that would execute a statement it produced.',
    text: 'Show me revenue for state DROP TABLE sites',
    expect: 'The value lands as a bound parameter and matches nothing. The table is untouched, and the chart just comes back empty.',
  },
  {
    q: 'What happens when it hallucinates a metric?',
    say: 'The provider rejects it before the request leaves, because the metric names are an enum in the tool schema. If one slips through, our server refuses it and hands the model the error, which it corrects on the next round. The person sees a slightly slower answer, not a failure.',
    expect: 'A "correcting" line in the chat, then the right chart.',
  },
  {
    q: 'Can it show a chart type we have not designed?',
    say: 'No. It names a kind from an enum, and if we have not registered a renderer under that name, nothing exists to draw it. That is the whole view-side security story — it cannot supply markup, a URL, or a component.',
    expect: 'Two independent gates: the manifest server-side, the registry client-side.',
  },
  {
    q: 'How much work is adding a new number?',
    say: 'Six lines of YAML. The tool schema, the system prompt, the number formatting and the colour of a delta all derive from that one entry. No prompt edit, no new tool, no frontend change.',
    expect: 'Show them the manifest — backend/board_manifest.yaml.',
  },
  {
    q: 'Is this locked to telco?',
    say: 'No. Everything you have seen is the SmartBoard framework, which is a separate repository. This deployment is a manifest, a tenancy hook, two pages and a seed script. The same engine drives a plantation console with a different YAML file.',
    expect: 'Point at github.com/oyj623/SmartBoard.',
  },
  {
    q: 'What does it cost per question?',
    say: 'Less than you would think, because the model never receives the data. It gets a result handle and a three-row preview, so a query returning 2,500 rows costs the same in tokens as one returning three. The browser fetches the rows directly.',
    expect: 'The trace lines in the chat show row counts and timings.',
  },
  {
    q: 'What if the model provider is down?',
    say: 'The board still runs. There is a deterministic keyword-matching fallback brain that drives the same pipeline — enough to demonstrate every mechanism below the model, which is also how the test suite runs with no network.',
    expect: 'The chip in the chat header says which brain is live.',
  },
]

export default { acts: DEMO_ACTS, faq: DEMO_FAQ }
