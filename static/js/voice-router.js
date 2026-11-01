/** Local intent routing for voice mode — no network, no model.
 *
 * This used to POST every unmatched utterance to `/voice/route` and have an
 * LLM classify it. Measured live, that call ran 1.5-3s typically but 12.3s and
 * 13.9s in the same batch of eight, and a timeout is worse than a wait: the
 * fallback silently used the *raw* transcript, so "easy two instance" reached
 * the pipeline uncorrected, or the turn was parked in the composer and nothing
 * happened. Tuning the cap only moved which turns broke.
 *
 * The job never needed a model. It is two mechanical problems:
 *
 *   1. Cloud jargon the browser mishears — a substitution table.
 *   2. Which UI command was meant — a matching problem over a list the client
 *      already holds.
 *
 * Everything else is a diagram instruction, and the diagram pipeline is
 * already the thing that understands natural language. Routing locally is
 * instant, free, deterministic, and cannot half-fail.
 */

/** Cloud terms browser speech recognition reliably mangles.
 *
 * Applied in order, so longer/more specific patterns must come first.
 * Deliberately conservative: a wrong substitution puts a service in the
 * diagram that the user never said, which is worse than leaving a word alone.
 */
const CLOUD_SPEECH_FIXES = [
    // "easy two" is not English — safe unconditionally. Bare "easy to" IS
    // English ("easy to read"), so it only converts when followed by a word
    // that makes it a compute instance. Getting this wrong would rewrite
    // ordinary speech into infrastructure.
    [/\beasy two\b/gi, 'EC2'],
    [/\bec two\b/gi, 'EC2'],
    [/\beasy to (instances?|boxes|box|servers?|nodes?|machines?|vms?)\b/gi, 'EC2 $1'],
    [/\bs three\b/gi, 'S3'],
    [/\bare d s\b/gi, 'RDS'],
    [/\bv p c\b/gi, 'VPC'],
    [/\be k s\b/gi, 'EKS'],
    [/\be c s\b/gi, 'ECS'],
    [/\ba l b\b/gi, 'ALB'],
    [/\bn l b\b/gi, 'NLB'],
    [/\bi a m\b/gi, 'IAM'],
    [/\bs q s\b/gi, 'SQS'],
    [/\bs n s\b/gi, 'SNS'],
    [/\bc d n\b/gi, 'CDN'],
    [/\bdynamo(?:\s+db)?\b/gi, 'DynamoDB'],
    [/\bcloud\s+front\b/gi, 'CloudFront'],
    [/\bcloud\s+watch\b/gi, 'CloudWatch'],
    [/\bcloud\s+formation\b/gi, 'CloudFormation'],
    [/\broute\s+(?:fifty[\s-]?three|53)\b/gi, 'Route 53'],
    [/\bmulti[\s-]?a\.?\s?z\b/gi, 'Multi-AZ'],
    [/\bmulti\s+easy\b/gi, 'Multi-AZ'],
    [/\bavailability\s+zones?\b/gi, 'Availability Zone'],
    [/\bmy\s+sequel\b/gi, 'MySQL'],
    [/\bpost\s+gres(?:\s*ql)?\b/gi, 'PostgreSQL'],
    [/\bno\s+sequel\b/gi, 'NoSQL'],
    [/\bsequel\s+server\b/gi, 'SQL Server'],
    [/\bkubernetes\b/gi, 'Kubernetes'],
    [/\bk eights\b/gi, 'Kubernetes'],
    [/\blamb da\b/gi, 'Lambda'],
    [/\bapi\s+gateway\b/gi, 'API Gateway'],
    [/\bg c p\b/gi, 'GCP'],
    [/\ba w s\b/gi, 'AWS'],
    // Product names the recogniser gets right but lower-cases. Cheap to fix
    // here, and it makes the read-back sound like the service rather than a
    // common noun.
    [/\bredis\b/gi, 'Redis'],
    [/\bkafka\b/gi, 'Kafka'],
    [/\bnginx\b/gi, 'Nginx'],
    [/\bmongo(?:\s*db)?\b/gi, 'MongoDB'],
    [/\bpostgres\b/gi, 'PostgreSQL'],
    [/\bazure\b/gi, 'Azure'],
];

/** Fix cloud vocabulary in a spoken transcript. Pure string work. */
function normalizeCloudSpeech(text) {
    let out = String(text || '');
    for (const [pattern, replacement] of CLOUD_SPEECH_FIXES) {
        out = out.replace(pattern, replacement);
    }
    return out.replace(/\s+/g, ' ').trim();
}

/** Capitalised, punctuated — it is read back to the user and sent as a prompt. */
function _asInstruction(text) {
    const t = String(text || '').trim();
    if (!t) return '';
    const head = t.charAt(0).toUpperCase() + t.slice(1);
    return /[.!?]$/.test(head) ? head : head + '.';
}

/** Best UI command for an utterance, or null.
 *
 * Deliberately strict: a command must have *every* token of one of its phrases
 * present in what was said. "turn on the cost estimate please" contains all of
 * "turn on cost", so it matches; "add a cost breakdown to the diagram" does
 * not. Loose matching here would silently flip settings the user never asked
 * about, which is far worse than falling through to a diagram edit.
 *
 * Single-word phrases are excluded — "stop", "undo" and friends are already
 * handled exactly and synchronously by voice-commands.js, and matching them
 * fuzzily would let "don't stop there" trigger a stop.
 */
function matchUiCommandLocally(utterance) {
    if (typeof VOICE_COMMANDS === 'undefined') return null;
    const said = new Set(normalizeVoiceText(utterance).split(' ').filter(Boolean));
    if (!said.size) return null;

    let best = null;
    let bestLen = 0;
    for (const cmd of VOICE_COMMANDS) {
        for (const phrase of (cmd.phrases || [])) {
            const tokens = phrase.split(' ').filter(Boolean);
            if (tokens.length < 2) continue;
            if (!tokens.every((t) => said.has(t))) continue;
            if (tokens.length > bestLen) {
                best = cmd;
                bestLen = tokens.length;
            }
        }
    }
    return best;
}

/** Route one utterance the exact-phrase registry did not claim.
 *
 * Async only so callers need no change — it never awaits anything. Returns the
 * same shape the old endpoint did, so voice.js is untouched.
 */
async function routeVoiceUtterance(transcript) {
    const raw = String(transcript || '').trim();
    if (!raw) return { kind: 'clarify', speak: "I didn't hear anything." };

    const cmd = matchUiCommandLocally(raw);
    if (cmd) {
        return { kind: 'ui_command', command_id: cmd.id, prompt: '', speak: cmd.label + '.' };
    }

    // Too short to be an instruction and not a command — almost always a
    // half-caught word rather than something to act on. Acting would mean
    // spending a full generation on noise.
    const words = normalizeVoiceText(raw).split(' ').filter(Boolean);
    if (words.length < 2) {
        return { kind: 'clarify', speak: "Sorry, I didn't catch that." };
    }

    // Everything else is an instruction for the diagram. The pipeline is the
    // component that understands natural language — it does not need a second
    // model to pre-chew the sentence, only the vocabulary corrected.
    const prompt = _asInstruction(normalizeCloudSpeech(raw));
    return { kind: 'diagram_turn', command_id: '', prompt, speak: 'Okay, updating the diagram.' };
}
