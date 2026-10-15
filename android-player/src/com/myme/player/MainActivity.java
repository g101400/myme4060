package com.myme.player;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.provider.DocumentsContract;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.LinkedList;
import java.util.List;
import java.util.Queue;

/**
 * myme 便携播放器（安卓端）
 * ------------------------------------------------------------
 * 只做一件事：用 WebView 打开包内的 player.html，把「完整版导出的便携播放包」
 * 原样搬到手机上离线播放。不联网、不申请网络权限、不依赖任何第三方库。
 *
 * 关键点（踩过的坑都写在这，别改回去）：
 *  · setMediaPlaybackRequiresUserGesture(false)：否则 video.play() 被浏览器策略拦掉，点播放没声音。
 *  · setAllowFileAccess(true) + loadUrl("file:///android_asset/...")：assets 免解压、免存储权限。
 *  · FLAG_KEEP_SCREEN_ON：演示时不能熄屏。
 *  · onShowCustomView / onHideCustomView：播放器里的全屏要交给原生容器，否则全屏按钮点了没反应。
 *  · configChanges 全接：横竖屏切换不重建 Activity，否则演示切屏就从头开始。
 *  · **onShowFileChooser 必须实现**（v2.8.0 修复的头号 bug）：player.html 里
 *    「📂 导入播放文件」的三个子菜单（zip / 文件夹 / 清单+素材）都是隐藏
 *    <input type="file"> 调 .click() 触发的。在 Android WebView 里，文件选择器
 *    **不会由渲染进程自己拉起**，必须由宿主实现 WebChromeClient.onShowFileChooser()
 *    代为转交系统选择器。宿主不实现 → 点击被静默丢弃 → 三个子菜单"点了没反应"。
 *    这一条与 setAllowFileAccess* 无关，也和页面代码无关，纯宿主职责。
 */
public class MainActivity extends Activity {

    /** 文件选择器请求码（onShowFileChooser → onActivityResult 配对） */
    private static final int REQ_FILE = 1001;

    private WebView web;
    private View customView;
    private WebChromeClient.CustomViewCallback customCallback;

    /** WebView 交给我们、等我们回填选择结果的一次性回调 */
    private ValueCallback<Uri[]> mFilePathCallback;
    /**
     * 本页要选的是「一个文件夹」还是「一个/多个文件」。
     * 为什么需要这个开关：WebView 的 FileChooserParams 只有
     * MODE_OPEN / MODE_OPEN_MULTIPLE / MODE_SAVE 三种模式，**没有"目录模式"信号**，
     * <input webkitdirectory> 过来时看到的仍是普通 MODE_OPEN，
     * 于是宿主无从分辨"选文件夹"还是"选文件"。所以由页面在点击前显式声明
     * （MymeNative.setFolderMode(true)），宿主才好改走 ACTION_OPEN_DOCUMENT_TREE。
     */
    private boolean mWantFolder = false;

    /** 暴露给页面的原生桥（页面据此判断"运行在安卓壳里"并声明选文件夹意图） */
    public class MymeNative {
        @JavascriptInterface
        public boolean isAndroid() {
            return true;
        }

        @JavascriptInterface
        public String platform() {
            return "android-webview";
        }

        @JavascriptInterface
        public void setFolderMode(boolean on) {
            mWantFolder = on;
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setDefaultTextEncodingName("utf-8");

        // 页面脚本通过 window.MymeNative 访问（仅本 App 自带的只读 assets 页面在用）
        web.addJavascriptInterface(new MymeNative(), "MymeNative");

        web.setBackgroundColor(Color.BLACK);
        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (customView != null) { callback.onCustomViewHidden(); return; }
                customView = view;
                customCallback = callback;
                FrameLayout root = (FrameLayout) getWindow().getDecorView();
                root.addView(view, new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            }

            @Override
            public void onHideCustomView() {
                if (customView == null) return;
                FrameLayout root = (FrameLayout) getWindow().getDecorView();
                root.removeView(customView);
                customView = null;
                if (customCallback != null) customCallback.onCustomViewHidden();
                customCallback = null;
            }

            /* ============================================================
               导入播放文件的命门：把 WebView 的文件选择请求转交系统选择器。
               ============================================================ */
            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> filePathCallback,
                                             FileChooserParams fileChooserParams) {
                // 同一时刻只允许一次选择：若上一次还没回来，先取消，避免回调泄漏
                if (mFilePathCallback != null) {
                    // ValueCallback<Uri[]> 没有 onCancel()：回 null 即"没选文件"，
                    // 页面 onchange 不会触发，也不会一直吊着上一次的等待。
                    mFilePathCallback.onReceiveValue(null);
                    mFilePathCallback = null;
                }
                mFilePathCallback = filePathCallback;

                Intent intent;
                if (mWantFolder) {
                    // 选文件夹：走 SAF 目录树（Android 5.0+），onActivityResult 里递归展开为文件列表
                    intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                            | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
                } else {
                    intent = new Intent(Intent.ACTION_GET_CONTENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType(pickMime(fileChooserParams));
                    if (fileChooserParams.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                    }
                }
                try {
                    startActivityForResult(Intent.createChooser(intent, "选择播放文件"), REQ_FILE);
                } catch (Exception e) {
                    // 设备上没有可用的选择器：明确回一个"取消"，别把页面吊死
                    mFilePathCallback = null;
                    tip("本机没有可用的文件选择器：" + e.getMessage());
                    filePathCallback.onReceiveValue(null);
                    return false;
                }
                return true;
            }
        });

        setContentView(web);
        web.loadUrl("file:///android_asset/player.html");
    }

    /** 把页面 <input accept="..."> 的类型收敛成一个能被系统选择器识别的 MIME。 */
    private String pickMime(WebChromeClient.FileChooserParams p) {
        try {
            String[] acc = p == null ? null : p.getAcceptTypes();
            if (acc != null) {
                for (String a : acc) {
                    if (a == null) continue;
                    for (String one : a.split("[,;]")) {
                        one = one.trim();
                        // 取第一个形如 application/zip 的项；".zip,.json" 这类后缀串不作数
                        if (one.contains("/")) return one;
                    }
                }
            }
        } catch (Exception ignore) { }
        return "*/*";
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != REQ_FILE) {
            super.onActivityResult(requestCode, resultCode, data);
            return;
        }
        boolean folder = mWantFolder;
        mWantFolder = false;                  // 无论成败都复位，避免状态串到下一次选择
        ValueCallback<Uri[]> cb = mFilePathCallback;
        mFilePathCallback = null;
        if (cb == null) return;

        if (resultCode != RESULT_OK || data == null || data.getData() == null) {
            cb.onReceiveValue(null);          // 用户取消：明确告诉页面"没选"，页面不会一直等
            return;
        }
        Uri uri = data.getData();

        if (folder) {
            List<Uri> files = new ArrayList<Uri>();
            try { collectTreeFiles(this, uri, files); } catch (Exception ignore) { }
            if (files.isEmpty()) {
                tip("该文件夹里没有可用文件");
                cb.onReceiveValue(null);
                return;
            }
            tip("已选中 " + files.size() + " 个文件");
            cb.onReceiveValue(files.toArray(new Uri[0]));
            return;
        }

        if (data.getClipData() != null) {      // 多选
            int n = data.getClipData().getItemCount();
            Uri[] arr = new Uri[n];
            for (int i = 0; i < n; i++) arr[i] = data.getClipData().getItemAt(i).getUri();
            cb.onReceiveValue(arr);
        } else {                               // 单选
            cb.onReceiveValue(new Uri[]{ uri });
        }
    }

    /**
     * 递归展开 OPEN_DOCUMENT_TREE 选中的目录，收集全部文件 Uri。
     * 只走 DocumentsContract + ContentResolver（系统自带），不引第三方库 —— 本壳零依赖。
     * guard 上限防止极端深的目录把主线程拖死。
     */
    private static void collectTreeFiles(Context ctx, Uri treeUri, List<Uri> out) {
        String rootId;
        try {
            rootId = DocumentsContract.getTreeDocumentId(treeUri);
        } catch (Exception e) {
            return;
        }
        Queue<String> dirs = new LinkedList<String>();
        dirs.add(rootId);
        int guard = 0;
        while (!dirs.isEmpty() && guard++ < 400) {
            String docId = dirs.poll();
            Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, docId);
            Cursor c = null;
            try {
                c = ctx.getContentResolver().query(children, new String[]{
                        DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                        DocumentsContract.Document.COLUMN_MIME_TYPE}, null, null, null);
                if (c == null) continue;
                while (c.moveToNext()) {
                    String id = c.getString(0);
                    String mime = c.getString(1);
                    if (DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)) {
                        dirs.add(id);
                    } else {
                        out.add(DocumentsContract.buildDocumentUriUsingTree(treeUri, id));
                    }
                }
            } catch (Exception ignore) {
            } finally {
                if (c != null) {
                    try { c.close(); } catch (Exception ignore) { }
                }
            }
        }
    }

    private void tip(String msg) {
        try { Toast.makeText(this, msg, Toast.LENGTH_SHORT).show(); } catch (Exception ignore) { }
    }

    @Override
    public void onBackPressed() {
        if (customView != null) {
            web.getWebChromeClient().onHideCustomView();
            return;
        }
        if (mFilePathCallback != null) {       // 选择器还没回来就按返回：先收掉回调
            mFilePathCallback.onReceiveValue(null);
            mFilePathCallback = null;
            return;
        }
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (web != null) web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    protected void onDestroy() {
        if (mFilePathCallback != null) {
            mFilePathCallback.onReceiveValue(null);
            mFilePathCallback = null;
        }
        if (web != null) {
            try { web.destroy(); } catch (Exception ignore) { }
            web = null;
        }
        super.onDestroy();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // 物理返回键优先退出全屏，再交回 WebView（播放器自身也有键盘处理）
        if (keyCode == KeyEvent.KEYCODE_BACK && customView != null) {
            onBackPressed();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
