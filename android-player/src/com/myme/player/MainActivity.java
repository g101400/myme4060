package com.myme.player;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

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
 */
public class MainActivity extends Activity {

    private WebView web;
    private View customView;
    private WebChromeClient.CustomViewCallback customCallback;

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
        });

        setContentView(web);
        web.loadUrl("file:///android_asset/player.html");
    }

    @Override
    public void onBackPressed() {
        if (customView != null) {
            web.getWebChromeClient().onHideCustomView();
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
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // 物理返回键优先退出全屏，再交回 WebView（播放器自身也有键盘处理）
        if (keyCode == KeyEvent.KEYCODE_BACK && customView != null) {
            onBackPressed();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
