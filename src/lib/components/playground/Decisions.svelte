<script lang="ts">
	import { getPageContext } from '$lib/utils/page';
	const { goto } = getPageContext();

	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { user } from '$lib/stores';
	import { askSystemOne, getSystemOneModels, type SystemOneQuestion } from '$lib/apis/systemone';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n: any = getContext('i18n');

	type QuestionRow = {
		id: string;
		type: SystemOneQuestion['type'];
		instructions: string;
		criteria: string;
	};
	type Bar = { label: string; value: number };
	type Result = { id: string; headline: string; confidence: number | null; bars: Bar[] };

	let loaded = false;
	let loading = false;

	let models: { id: string; name: string }[] = [];
	let selectedModelId = '';

	let state = '';
	let questions: QuestionRow[] = [{ id: 'q1', type: 'choice', instructions: '', criteria: '' }];

	let results: Result[] = [];
	let latency: number | null = null;
	let inputTokens: number | null = null;

	const inputClass =
		'w-full bg-transparent border border-gray-100/30 dark:border-gray-850/30 outline-hidden rounded-lg text-sm px-2 py-1';

	const addQuestion = () => {
		let n = questions.length + 1;
		while (questions.some((q) => q.id === `q${n}`)) n++;
		questions = [...questions, { id: `q${n}`, type: 'choice', instructions: '', criteria: '' }];
	};

	const criteriaOf = (q: QuestionRow) =>
		q.criteria
			.split('\n')
			.map((c) => c.trim())
			.filter(Boolean);

	// "name: description" per line; a line without a colon describes itself.
	const describe = (line: string): [string, string] => {
		const at = line.indexOf(':');
		return at < 0 ? [line, line] : [line.slice(0, at).trim(), line.slice(at + 1).trim()];
	};

	const buildQuestions = () => {
		const body: Record<string, SystemOneQuestion> = {};
		for (const q of questions) {
			const id = q.id.trim();
			if (!id || id in body) {
				throw $i18n.t('Every question needs a unique ID');
			}
			if (!q.instructions.trim()) {
				throw $i18n.t('Every question needs instructions');
			}
			const criteria = criteriaOf(q);
			if (q.type !== 'noul' && criteria.length < 2) {
				throw $i18n.t('A choice or score question needs at least two criteria');
			}
			body[id] = {
				type: q.type,
				instructions: q.instructions.trim(),
				...(q.type === 'score' && { criteria }),
				...(q.type === 'choice' && { criteria: Object.fromEntries(criteria.map(describe)) })
			};
		}
		return body;
	};

	// A score answers keyed by level index with a legend; a noul is one number.
	const toResult = (id: string, answer: any): Result => {
		if (typeof answer?.noul === 'number') {
			return {
				id,
				headline: answer.noul.toFixed(2),
				confidence: null,
				bars: [{ label: 'noul', value: answer.noul }]
			};
		}
		const legend = answer?.legend ?? {};
		const bars = Object.entries(answer?.probabilities ?? {})
			.map(([key, value]) => ({ label: `${legend[key] ?? key}`, value: Number(value) }))
			.sort((a, b) => b.value - a.value);
		return {
			id,
			headline: `${answer?.choice ?? bars[0]?.label ?? ''}`,
			confidence: typeof answer?.confidence === 'number' ? answer.confidence : null,
			bars
		};
	};

	const submitHandler = async () => {
		let body;
		try {
			body = buildQuestions();
		} catch (error) {
			toast.error(`${error}`);
			return;
		}

		loading = true;
		const start = performance.now();
		const res = await askSystemOne(localStorage.token, {
			model: selectedModelId,
			state,
			questions: body
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		loading = false;

		if (res) {
			latency = Math.round(performance.now() - start);
			inputTokens = res.usage?.input_tokens ?? null;
			results = Object.keys(body).map((id) => toResult(id, res.answers?.[id]));
		}
	};

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/');
			return;
		}

		models = await getSystemOneModels(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return [];
		});
		selectedModelId = models[0]?.id ?? '';
		loaded = true;
	});
</script>

<div class="flex flex-col w-full h-full px-2.5 pb-3">
	{#if !loaded}
		<div class="flex h-full justify-center">
			<div class="my-auto"><Spinner className="size-6" /></div>
		</div>
	{:else}
		<div class="flex-1 min-h-0 overflow-y-auto flex flex-col md:flex-row gap-4 pt-0.5 pb-2.5">
			<div class="flex-1 min-w-0 flex flex-col gap-3">
				<div class="flex flex-col gap-1">
					<label class="text-xs text-gray-600 dark:text-gray-400" for="systemone-state">
						{$i18n.t('State')}
					</label>
					<textarea
						id="systemone-state"
						class="{inputClass} h-40 resize-y p-3"
						bind:value={state}
						placeholder={$i18n.t('What the model should decide about')}
					></textarea>
				</div>

				<div class="flex items-center justify-between">
					<div class="text-xs text-gray-600 dark:text-gray-400">{$i18n.t('Questions')}</div>
					<button
						class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-black/5 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/5 dark:hover:text-gray-300"
						type="button"
						aria-label={$i18n.t('Add Question')}
						on:click={addQuestion}
					>
						<Plus />
					</button>
				</div>

				{#each questions as question, idx (idx)}
					<div
						class="flex flex-col gap-1.5 rounded-lg border border-gray-100/30 dark:border-gray-850/30 p-2"
					>
						<div class="flex gap-1.5 items-center">
							<input
								class="{inputClass} max-w-32"
								bind:value={question.id}
								placeholder={$i18n.t('ID')}
								aria-label={$i18n.t('ID')}
							/>
							<select class={inputClass} bind:value={question.type} aria-label={$i18n.t('Type')}>
								<option value="choice" class="bg-gray-50 dark:bg-gray-700">choice</option>
								<option value="score" class="bg-gray-50 dark:bg-gray-700">score</option>
								<option value="noul" class="bg-gray-50 dark:bg-gray-700">noul</option>
							</select>
							{#if questions.length > 1}
								<button
									class="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
									type="button"
									aria-label={$i18n.t('Remove')}
									on:click={() => {
										questions = questions.filter((_, i) => i !== idx);
									}}
								>
									<XMark className="size-4" />
								</button>
							{/if}
						</div>
						<input
							class={inputClass}
							bind:value={question.instructions}
							placeholder={$i18n.t('Instructions')}
							aria-label={$i18n.t('Instructions')}
						/>
						{#if question.type !== 'noul'}
							<textarea
								class="{inputClass} h-20 resize-y"
								bind:value={question.criteria}
								placeholder={question.type === 'choice'
									? $i18n.t('Options, one per line: name: description')
									: $i18n.t('Levels, one per line, lowest first')}
								aria-label={$i18n.t('Criteria')}
							></textarea>
						{/if}
					</div>
				{/each}
			</div>

			<div class="flex-1 min-w-0 flex flex-col gap-3">
				{#if results.length === 0}
					<div class="text-xs text-gray-400 dark:text-gray-600">
						{models.length === 0
							? $i18n.t(
									'No System One models found. Add a connection in Admin Settings > Connections.'
								)
							: $i18n.t('Answers appear here')}
					</div>
				{:else}
					<div class="text-xs text-gray-600 dark:text-gray-400">
						{latency} ms{#if inputTokens !== null}
							· {inputTokens} {$i18n.t('Tokens')}{/if}
					</div>
					{#each results as result (result.id)}
						<div class="flex flex-col gap-1">
							<div class="flex items-baseline justify-between gap-2 text-sm">
								<div class="truncate">
									<span class="text-gray-400 dark:text-gray-600">{result.id}</span>
									<span class="font-medium">{result.headline}</span>
								</div>
								{#if result.confidence !== null}
									<div class="shrink-0 text-xs text-gray-600 dark:text-gray-400">
										{$i18n.t('Confidence')}
										{result.confidence.toFixed(2)}
									</div>
								{/if}
							</div>
							{#each result.bars as bar}
								<div class="flex items-center gap-2 text-xs">
									<div class="w-32 shrink-0 truncate text-gray-600 dark:text-gray-400">
										{bar.label}
									</div>
									<div class="flex-1 h-1.5 rounded-full bg-gray-100 dark:bg-gray-850">
										<div
											class="h-full rounded-full bg-gray-900 dark:bg-gray-100"
											style="width: {Math.max(0, Math.min(1, bar.value)) * 100}%"
										></div>
									</div>
									<div class="w-10 shrink-0 text-right tabular-nums">{bar.value.toFixed(2)}</div>
								</div>
							{/each}
						</div>
					{/each}
				{/if}
			</div>
		</div>

		<div class="flex justify-between items-center">
			<div class="flex-1">
				<select
					class="bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg py-1 px-2 -mx-0.5 text-sm outline-hidden w-full"
					aria-label={$i18n.t('Model')}
					bind:value={selectedModelId}
				>
					{#each models as model}
						<option value={model.id} class="bg-gray-50 dark:bg-gray-700">{model.id}</option>
					{/each}
				</select>
			</div>

			<div class="flex gap-2 shrink-0 ml-2">
				<button
					class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
					disabled={loading || !selectedModelId}
					on:click={submitHandler}
				>
					{#if loading}<Spinner className="size-3.5" />{/if}
					{$i18n.t('Run')}
				</button>
			</div>
		</div>
	{/if}
</div>
