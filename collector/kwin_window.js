let activeWindow = null;

function sendWindow() {
    const snapshot = activeWindow === null ? null : {
        title: activeWindow.caption,
        pid: activeWindow.pid,
        resource_class: activeWindow.resourceClass
    };
    callDBus(
        "org.workgraph.Collector",
        "/org/workgraph/Collector",
        "org.workgraph.Collector",
        "UpdateWindow",
        JSON.stringify(snapshot)
    );
}

function activateWindow(window) {
    if (activeWindow !== null) {
        try {
            activeWindow.captionChanged.disconnect(sendWindow);
        } catch (error) {
        }
    }
    activeWindow = window;
    if (activeWindow !== null) {
        activeWindow.captionChanged.connect(sendWindow);
    }
    sendWindow();
}

workspace.windowActivated.connect(activateWindow);
activateWindow(workspace.activeWindow);