{
	"translatorID": "8f392a92-cade-41d1-a8b5-c617f5b4a261",
	"label": "PaperRadar",
	"creator": "PaperRadar contributors",
	"target": "^http://(?:127\\.0\\.0\\.1|localhost)(?::[0-9]+)?/($|[?#])",
	"minVersion": "7.0",
	"maxVersion": "",
	"priority": 50,
	"inRepository": false,
	"translatorType": 4,
	"browserSupport": "gcsibv",
	"lastUpdated": "2026-10-12 18:00:00"
}

/** Read the app's versioned DOM context; never request arbitrary API paths.
 * @returns {{version:number, mode:string, endpoint:string, count:number}|null}
 */
function getContext(doc) {
	const node = doc.querySelector('#research-tagger-zotero');
	if (!node) return null;
	const context = JSON.parse(node.textContent);
	if (!context) return null;
	if (context.version !== 1 || !['single', 'multiple'].includes(context.mode)
		|| !Number.isInteger(context.count) || context.count < 1
		|| !/^\/api\/conferences\/[1-9][0-9]*\/zotero\?[^#]*$/.test(context.endpoint)) {
		throw new Error('Unsupported PaperRadar context. Refresh the app and update the translator.');
	}
	return context;
}

/** Detect only supported loopback pages with an active paper/list context. */
function detectWeb(doc, url) {
	if (!/^http:\/\/(?:127\.0\.0\.1|localhost)(?::[0-9]+)?\/(?:$|[?#])/.test(url)) return false;
	const context = getContext(doc);
	return context ? (context.mode === 'single' ? 'conferencePaper' : 'multiple') : false;
}

/** Fetch the captured selection once; Zotero chooses and saves items, including PDF URLs. */
async function doWeb(doc, url) {
	if (!detectWeb(doc, url)) throw new Error('No PaperRadar papers are selected.');
	const context = getContext(doc);
	const origin = url.match(/^http:\/\/[^/]+/)[0];
	const data = await requestJSON(origin + context.endpoint);
	if (data.version !== 1 || !Array.isArray(data.papers)) throw new Error('Invalid PaperRadar response.');
	if (context.mode === 'single' && data.papers.length !== 1) throw new Error('The selected paper is no longer available.');
	if (!data.papers.length) throw new Error('No papers match these filters anymore.');
	let papers = data.papers;
	if (context.mode === 'multiple') {
		const choices = {};
		for (const paper of papers) choices[paper.id] = paper.title + (paper.pdf_url ? '' : ' [no PDF link]');
		const selected = await Zotero.selectItems(choices);
		if (!selected) return;
		papers = papers.filter(paper => Object.prototype.hasOwnProperty.call(selected, paper.id));
	}
	for (const paper of papers) await savePaper(paper);
}

/** Map one version-1 API record to a conferencePaper; downloads are owned by Zotero.
 * @param {object} paper Saved metadata with id, title, abstract, conference, year,
 * source_url, pdf_url, arxiv_id, and tags. Authors/DOIs are not currently collected.
 */
async function savePaper(paper) {
	const item = new Zotero.Item('conferencePaper');
	item.title = paper.title;
	item.abstractNote = paper.abstract || '';
	item.conferenceName = paper.conference;
	item.date = String(paper.year);
	item.url = paper.pdf_url || (paper.arxiv_id ? `https://arxiv.org/abs/${paper.arxiv_id}` : paper.source_url);
	item.tags = paper.tags.map(tag => ({tag}));
	item.libraryCatalog = 'PaperRadar';
	const escape = value => value.replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
	item.notes = [{note: `Conference page: ${escape(paper.source_url)}`}];
	if (paper.arxiv_id) {
		item.extra = `arXiv: ${paper.arxiv_id}`;
		item.notes.push({note: `arXiv page: https://arxiv.org/abs/${escape(paper.arxiv_id)}`});
	}
	item.attachments = paper.pdf_url ? [{title:'Full Text PDF', url:paper.pdf_url, mimeType:'application/pdf', proxy:false}] : [];
	await item.complete();
}
