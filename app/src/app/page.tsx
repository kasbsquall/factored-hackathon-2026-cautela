import Link from "next/link";
import { ArrowRight, ArrowUpRight, ChartBar, ChatsCircle, Eyeglasses, Flask, Headset, ListChecks, Signpost } from "@phosphor-icons/react/dist/ssr";
import { DialMark } from "@/components/brand/dial-mark";
import styles from "./home.module.css";

const MODE = process.env.NEXT_PUBLIC_API_MODE === "live" ? "live" : "mock";

export default function Home() {
  return (
    <main className={styles.page}>
      <header className={styles.top}>
        <span className={styles.brand}>
          <DialMark size={24} />
          Cautela
        </span>
        <span className={styles.mode}>
          <Flask weight="light" aria-hidden />
          {MODE === "mock" ? "Synthetic data · mock API" : "Synthetic data · live API"}
        </span>
      </header>

      <section className={`${styles.hero} rise`}>
        <DialMark size={120} label="Cautela dial mark" />
        <h1 className={styles.title}>
          Acts on what it can verify.
          <span className={styles.muted}> Asks when unsure. Hands the rest to a person with the facts already checked.</span>
        </h1>
      </section>

      <section className={`${styles.primary} rise`} style={{ "--i": 1 } as React.CSSProperties} aria-labelledby="try-title">
        <div className={styles.primaryText}>
          <p className="eyebrow">Customer · Spanish and Portuguese</p>
          <h2 id="try-title" className={styles.h2}>Dispute a charge as a test customer</h2>
          <p className={styles.body}>
            Log in with a synthetic identity, pick the charge from ranked candidates, see the merchant evidence, confirm, and get
            a receipt that was read back from the case store. Each scenario takes under a minute.
          </p>
          <p className={styles.reviewer}>
            <Eyeglasses weight="light" aria-hidden />
            <span>
              The conversation stays in Spanish or Portuguese, as the customer would use it.{" "}
              <Link href="/customer?review=en">Open it with English labels</Link>, a plain-English account of each step, and an
              English translation under each message you type or receive.
            </span>
          </p>
        </div>
        <Link href="/customer" className={styles.cta}>
          <ChatsCircle weight="light" aria-hidden />
          <span>Open the customer conversation</span>
          <span className={styles.nest}><ArrowRight weight="light" aria-hidden /></span>
        </Link>
      </section>

      <nav className={`${styles.more} rise`} style={{ "--i": 2 } as React.CSSProperties} aria-label="Other surfaces">
        <ul className={styles.list}>
          <li>
            <Link href="/console" className={styles.row}>
              <Headset weight="light" aria-hidden />
              <span className={styles.rowText}>
                <span className={styles.rowName}>Agent console</span>
                <span className={styles.rowDetail}>Handoff queue and the ready file: verified facts, actions with their status, open questions</span>
              </span>
              <ArrowUpRight weight="light" aria-hidden />
            </Link>
          </li>
          <li>
            <Link href="/insights" className={styles.row}>
              <ChartBar weight="light" aria-hidden />
              <span className={styles.rowText}>
                <span className={styles.rowName}>Insights</span>
                <span className={styles.rowDetail}>Why disputes first: complaint volume, SLA breach, resolution time, channels, demand, and the offline evaluation with its caveats</span>
              </span>
              <ArrowUpRight weight="light" aria-hidden />
            </Link>
          </li>
          <li>
            <Link href="/insights#decides" className={styles.row}>
              <Signpost weight="light" aria-hidden />
              <span className={styles.rowText}>
                <span className={styles.rowName}>How Cautela decides</span>
                <span className={styles.rowDetail}>What it does alone, when it asks, when it needs a yes and when a person takes over, with every threshold and claim window</span>
              </span>
              <ArrowUpRight weight="light" aria-hidden />
            </Link>
          </li>
          <li>
            <Link href="/audit" className={styles.row}>
              <ListChecks weight="light" aria-hidden />
              <span className={styles.rowText}>
                <span className={styles.rowName}>Audit trail</span>
                <span className={styles.rowDetail}>Every step of one case with rule ids, tool calls, verification, latency, LLM cost and the hash chain</span>
              </span>
              <ArrowUpRight weight="light" aria-hidden />
            </Link>
          </li>
        </ul>
      </nav>
    </main>
  );
}
