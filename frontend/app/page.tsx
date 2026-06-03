// TODO: prompt screen — search bar + Run button
// TODO: prompt screen — search bar + Run button
import { SignInButton, SignOutButton } from "@clerk/nextjs";
import { auth } from "@clerk/nextjs/server";

export default async function HomePage() {
  const { userId } = await auth();
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <h1 className="text-3xl font-semibold tracking-tight">Lens</h1>
      <p className="mt-2 text-sm text-muted-foreground">Prompt screen — coming soon</p>
      <div className="mt-4">
        {userId ? <SignOutButton /> : <SignInButton />}
      </div>
    </main>
  );
}
