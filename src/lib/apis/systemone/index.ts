import { SYSTEMONE_API_BASE_URL } from '$lib/constants';
import { getErrorMessage } from '$lib/apis/openai';

const request = async (token: string, path: string, body?: object) => {
	let error = null;

	const res = await fetch(`${SYSTEMONE_API_BASE_URL}${path}`, {
		method: body ? 'POST' : 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		...(body && { body: JSON.stringify(body) })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = getErrorMessage(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

type SystemOneConfig = {
	ENABLE_SYSTEMONE_API: boolean;
	SYSTEMONE_API_BASE_URLS: string[];
	SYSTEMONE_API_KEYS: string[];
	SYSTEMONE_API_CONFIGS: object;
};

export type SystemOneQuestion = {
	type: 'choice' | 'score' | 'noul';
	instructions: string;
	// choice: option name -> its description; score: the levels, in order
	criteria?: Record<string, string> | string[];
};

export type SystemOneRequest = {
	model: string;
	state: string;
	questions: Record<string, SystemOneQuestion>;
};

export const getSystemOneConfig = (token: string = '') => request(token, '/config');

export const updateSystemOneConfig = (token: string = '', config: SystemOneConfig) =>
	request(token, '/config/update', config);

export const getSystemOneModels = async (token: string = '') =>
	(await request(token, '/models'))?.data ?? [];

export const askSystemOne = (token: string = '', body: SystemOneRequest) =>
	request(token, '/v1/systemone', body);
