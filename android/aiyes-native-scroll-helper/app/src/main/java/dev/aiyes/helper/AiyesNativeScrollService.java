package dev.aiyes.helper;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Rect;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.HashMap;
import java.util.Map;

public final class AiyesNativeScrollService extends AccessibilityService {
    private static volatile AiyesNativeScrollService activeService;

    static AiyesNativeScrollService getActiveService() {
        return activeService;
    }

    @Override
    protected void onServiceConnected() {
        activeService = this;
    }

    @Override
    public void onDestroy() {
        if (activeService == this) {
            activeService = null;
        }
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // Commands arrive through NativeScrollReceiver.
    }

    @Override
    public void onInterrupt() {
        // No long-running feedback channel.
    }

    public boolean performNativeScroll(String stableId, int actionId) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null || stableId.isEmpty() || actionId == 0) {
            return false;
        }
        try {
            AccessibilityNodeInfo target = findByStableId(root, parseStableId(stableId));
            return target != null && target.performAction(actionId);
        } finally {
            root.recycle();
        }
    }

    private static AccessibilityNodeInfo findByStableId(
            AccessibilityNodeInfo node,
            Map<String, String> selector
    ) {
        if (matches(node, selector)) {
            return node;
        }
        for (int index = 0; index < node.getChildCount(); index++) {
            AccessibilityNodeInfo child = node.getChild(index);
            if (child == null) {
                continue;
            }
            AccessibilityNodeInfo found = findByStableId(child, selector);
            if (found != null) {
                return found;
            }
            child.recycle();
        }
        return null;
    }

    private static boolean matches(AccessibilityNodeInfo node, Map<String, String> selector) {
        String rid = selector.get("android:rid");
        if (rid != null && !rid.isEmpty() && !rid.equals(stringValue(node.getViewIdResourceName()))) {
            return false;
        }

        String className = selector.get("class");
        if (className != null && !className.isEmpty() && !className.equals(stringValue(node.getClassName()))) {
            return false;
        }

        String name = selector.get("name");
        if (name != null && !name.isEmpty() && !name.equals(nodeName(node))) {
            return false;
        }

        String bounds = selector.get("bounds");
        if (bounds != null && !bounds.isEmpty() && !bounds.equals(boundsText(node))) {
            return false;
        }

        return true;
    }

    private static String nodeName(AccessibilityNodeInfo node) {
        CharSequence description = node.getContentDescription();
        if (description != null && description.length() > 0) {
            return description.toString();
        }
        return stringValue(node.getText());
    }

    private static String boundsText(AccessibilityNodeInfo node) {
        Rect rect = new Rect();
        node.getBoundsInScreen(rect);
        int width = rect.right - rect.left;
        int height = rect.bottom - rect.top;
        return rect.left + "," + rect.top + "," + width + "," + height;
    }

    private static String stringValue(CharSequence value) {
        return value == null ? "" : value.toString();
    }

    private static Map<String, String> parseStableId(String stableId) {
        Map<String, String> result = new HashMap<>();
        String[] parts = stableId.split(";");
        for (String part : parts) {
            int separator = part.indexOf('=');
            if (separator <= 0) {
                continue;
            }
            result.put(part.substring(0, separator), part.substring(separator + 1));
        }
        return result;
    }
}
