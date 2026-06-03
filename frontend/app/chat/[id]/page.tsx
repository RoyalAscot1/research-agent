// TODO: chat screen — report card (sentiment scores, summary, sources) + follow-up chat
export default async function ChatPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <h1 className="text-2xl font-semibold tracking-tight">Report</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Chat screen for report <code>{id}</code> — coming soon
      </p>
    </main>
  );
}
