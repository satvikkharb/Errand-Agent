import { redirect } from "next/navigation";

export default function Home() {
  // Chat guards itself and sends signed-out visitors to /login.
  redirect("/chat");
}
