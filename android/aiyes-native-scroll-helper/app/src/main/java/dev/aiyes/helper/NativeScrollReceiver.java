package dev.aiyes.helper;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class NativeScrollReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        AiyesNativeScrollService service = AiyesNativeScrollService.getActiveService();
        if (service == null) {
            setResultCode(2);
            setResultData("aiyes helper accessibility service is not active");
            return;
        }

        String stableId = intent.getStringExtra("stable_id");
        int actionId = intent.getIntExtra("action_id", 0);
        boolean ok = service.performNativeScroll(stableId == null ? "" : stableId, actionId);
        setResultCode(ok ? 0 : 1);
        setResultData(ok ? "native scroll performed" : "native scroll target not found");
    }
}
