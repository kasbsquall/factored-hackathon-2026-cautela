import type { Metadata } from "next";
import { CustomerApp } from "@/components/customer/customer-app";

export const metadata: Metadata = { title: "Cliente" };

export default function CustomerPage() {
  return <CustomerApp />;
}
