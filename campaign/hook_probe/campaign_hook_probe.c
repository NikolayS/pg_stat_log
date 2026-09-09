/* Test-only hook, not production code. Counters are per-backend. */
#include "postgres.h"
#include "fmgr.h"
#include "utils/guc.h"

PG_MODULE_MAGIC;

static emit_log_hook_type previous_hook = NULL;
static bool nested_enabled = false;
static bool suppress_enabled = false;
static bool nested_active = false;
static int64 calls = 0;

static void
probe_hook(ErrorData *edata)
{
    if (edata->sqlerrcode == MAKE_SQLSTATE('Z','7','0','0','1') ||
        edata->sqlerrcode == MAKE_SQLSTATE('Z','7','0','0','2') ||
        edata->sqlerrcode == MAKE_SQLSTATE('Z','7','0','0','3'))
        calls++;

    if (nested_enabled && !nested_active &&
        edata->sqlerrcode == MAKE_SQLSTATE('Z','7','0','0','1'))
    {
        nested_active = true;
        PG_TRY();
        {
            ereport(WARNING,
                    (errcode(MAKE_SQLSTATE('Z','7','0','0','2')),
                     errmsg("campaign nested hook warning")));
        }
        PG_FINALLY();
        {
            nested_active = false;
        }
        PG_END_TRY();
    }
    if (suppress_enabled && edata->sqlerrcode == MAKE_SQLSTATE('Z','7','0','0','3'))
        edata->output_to_server = false;
    if (previous_hook)
        previous_hook(edata);
}

void _PG_init(void);
void
_PG_init(void)
{
    DefineCustomBoolVariable("campaign_hook_probe.nested", "Emit guarded nested warning.",
                             NULL, &nested_enabled, false, PGC_SUSET, 0, NULL, NULL, NULL);
    DefineCustomBoolVariable("campaign_hook_probe.suppress", "Suppress marker server log output.",
                             NULL, &suppress_enabled, false, PGC_SUSET, 0, NULL, NULL, NULL);
    previous_hook = emit_log_hook;
    emit_log_hook = probe_hook;
}

PG_FUNCTION_INFO_V1(campaign_hook_probe_calls);
Datum
campaign_hook_probe_calls(PG_FUNCTION_ARGS)
{
    PG_RETURN_INT64(calls);
}
