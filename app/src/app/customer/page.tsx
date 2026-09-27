import type { Metadata } from "next";
import { CustomerApp } from "@/components/customer/customer-app";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/** The reviewer link (?review=en) gets an English tab title from the server; the app keeps it in step after that. */
export async function generateMetadata({ searchParams }: { searchParams: SearchParams }): Promise<Metadata> {
  const { review } = await searchParams;
  return { title: review === "en" ? "Customer view" : "Cliente" };
}

export default function CustomerPage() {
  return <CustomerApp />;
}
