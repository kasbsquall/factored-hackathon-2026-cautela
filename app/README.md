# Cautela app

Next.js frontend: the customer conversation (`/customer`), the agent console, the audit view and the insights page. The repository README covers setup, the design system and how the app talks to `api/`. `npm run dev` starts it on port 3100; `NEXT_PUBLIC_API_MODE=mock` runs it without the backend.

## Accessibility

What the customer view offers for people who cannot see the screen well, and why.

- **Read aloud.** Every assistant message and every outcome receipt has a Listen button (pause, resume, stop). A "Read replies aloud" switch at the top of the chat reads each new reply as it arrives; this browser remembers the choice. It uses the browser's Web Speech API (`speechSynthesis`) with on-device voices only: voices the browser marks as network voices are never picked, so no text goes to a server. The voice matches the conversation language (es-MX, es-CO, es-AR, then any Spanish voice; pt-BR, then any Portuguese voice). One utterance plays at a time. When a device has no local voice for the language, the Listen buttons are hidden and the switch is disabled with the reason written next to it. Code: `src/lib/speech.ts`, `src/components/customer/read-aloud.tsx`, `auto-read.tsx`.
- **Receipt that can be kept.** The case number is large, can be copied (clipboard API, with the older copy command as fallback and a message when neither works) and is spelled out character by character when read aloud.
- **Screen readers.** A polite live region announces each new assistant message in its language; the message field, buttons and switches have labels; option, recognition and confirmation cards are native buttons that work with the keyboard; the card that answers the customer takes focus; every control shows the same brass focus ring as its hover state.
- **No custom dictation.** Speech to text is left to the operating system (keyboard dictation on phones and desktops) and to screen readers, which already do it well. Browser dictation (`webkitSpeechRecognition` in Chrome) sends the audio to a third-party server, which a bank conversation should not do. The message field is a plain labelled `textarea`, so OS dictation works in it; Enter sends and Shift+Enter adds a line.
