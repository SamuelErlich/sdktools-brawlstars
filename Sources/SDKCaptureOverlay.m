#import "SDKCaptureOverlay.h"
#import "SDKCaptureManager.h"
#import <QuartzCore/QuartzCore.h>
#import <dispatch/dispatch.h>

@class SDKCapturePanelController;

@interface SDKCaptureWindow : UIWindow
@end

@implementation SDKCaptureWindow
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    UIView *hit = [super hitTest:point withEvent:event];
    // An explicitly opened share sheet must remain interactive. Outside our
    // controls, ordinary game events pass through to the original window.
    if (self.rootViewController.presentedViewController) return hit;
    if (!hit || hit == self.rootViewController.view) return nil;
    return hit;
}
@end

@interface SDKCapturePanelController : UIViewController
@property(nonatomic, strong) UIButton *toggle;
@property(nonatomic, strong) UIView *panel;
@property(nonatomic, strong) UILabel *status;
@property(nonatomic, strong) UIButton *startButton;
@property(nonatomic, strong) UIButton *snapshotButton;
@property(nonatomic, strong) UIButton *markerButton;
@property(nonatomic, strong) UIButton *stopButton;
@property(nonatomic, strong) UIButton *exportButton;
@property(nonatomic, strong) NSTimer *statusTimer;
@property(nonatomic) BOOL dockRight;
@property(nonatomic) CGFloat verticalFraction;
@property(nonatomic) BOOL dragging;
- (void)setFeedbackActive:(BOOL)active;
- (void)refreshStatus;
@end

@implementation SDKCapturePanelController
- (instancetype)init {
    if ((self = [super init])) {
        _dockRight = YES;
        _verticalFraction = 0.28;
    }
    return self;
}
- (void)loadView {
    self.view = [[UIView alloc] initWithFrame:CGRectZero];
    self.view.backgroundColor = UIColor.clearColor;
    self.view.accessibilityViewIsModal = NO;
}
- (UIButton *)button:(NSString *)title action:(SEL)action {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    [button setTitle:title forState:UIControlStateNormal];
    [button setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
    button.titleLabel.font = [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold];
    button.titleLabel.adjustsFontSizeToFitWidth = YES;
    button.titleLabel.minimumScaleFactor = 0.75;
    button.contentEdgeInsets = UIEdgeInsetsMake(4, 7, 4, 7);
    button.backgroundColor = [UIColor colorWithRed:0.16 green:0.22 blue:0.32 alpha:1];
    button.layer.cornerRadius = 10;
    button.accessibilityLabel = title;
    [button addTarget:self action:action forControlEvents:UIControlEventTouchUpInside];
    return button;
}
- (void)viewDidLoad {
    [super viewDidLoad];
    self.toggle = [self button:@"SDK" action:@selector(togglePanel:)];
    self.toggle.layer.cornerRadius = 22;
    self.toggle.backgroundColor = [UIColor colorWithRed:0.10 green:0.40 blue:0.72 alpha:0.96];
    self.toggle.accessibilityHint = @"Toque para abrir. Arraste para mover o menu.";
    UIPanGestureRecognizer *drag = [[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(dragToggle:)];
    drag.cancelsTouchesInView = YES;
    [self.toggle addGestureRecognizer:drag];
    [self.view addSubview:self.toggle];

    self.panel = [[UIView alloc] initWithFrame:CGRectZero];
    self.panel.backgroundColor = [UIColor colorWithRed:0.055 green:0.075 blue:0.12 alpha:0.98];
    self.panel.layer.cornerRadius = 18;
    self.panel.layer.borderWidth = 1;
    self.panel.layer.borderColor = [UIColor colorWithWhite:1 alpha:0.15].CGColor;
    self.panel.hidden = YES;
    [self.view addSubview:self.panel];

    UILabel *heading = [[UILabel alloc] initWithFrame:CGRectZero];
    heading.tag = 1001;
    heading.text = @"SDK · Captura de diagnóstico";
    heading.textColor = UIColor.whiteColor;
    heading.font = [UIFont systemFontOfSize:16 weight:UIFontWeightBold];
    heading.adjustsFontSizeToFitWidth = YES;
    heading.minimumScaleFactor = 0.8;
    [self.panel addSubview:heading];

    self.status = [[UILabel alloc] initWithFrame:CGRectZero];
    self.status.textColor = [UIColor colorWithWhite:0.83 alpha:1];
    self.status.font = [UIFont systemFontOfSize:13];
    self.status.numberOfLines = 4;
    self.status.adjustsFontSizeToFitWidth = YES;
    self.status.minimumScaleFactor = 0.8;
    [self.panel addSubview:self.status];

    self.startButton = [self button:@"Iniciar 90 s" action:@selector(startPressed:)];
    self.snapshotButton = [self button:@"Ler agora" action:@selector(snapshotPressed:)];
    self.markerButton = [self button:@"Marcar momento" action:@selector(markerPressed:)];
    self.stopButton = [self button:@"Parar" action:@selector(stopPressed:)];
    self.exportButton = [self button:@"Exportar JSON + CSV" action:@selector(exportPressed:)];
    self.exportButton.accessibilityHint = @"Exporta a captura de interface e as tabelas estáticas verificadas do aplicativo.";
    UIButton *close = [self button:@"Fechar painel" action:@selector(togglePanel:)];
    close.tag = 1002;
    for (UIButton *button in @[self.startButton, self.snapshotButton, self.markerButton,
                               self.stopButton, self.exportButton, close]) [self.panel addSubview:button];
    [self refreshStatus];
}
- (CGRect)usableBounds {
    CGRect result = UIEdgeInsetsInsetRect(self.view.bounds, self.view.safeAreaInsets);
    if (result.size.width < 70 || result.size.height < 70) result = self.view.bounds;
    return CGRectInset(result, 8, 8);
}
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGRect usable = [self usableBounds];
    CGFloat buttonWidth = 56, buttonHeight = 44;
    if (!self.dragging) {
        CGFloat x = self.dockRight ? CGRectGetMaxX(usable) - buttonWidth : CGRectGetMinX(usable);
        CGFloat y = CGRectGetMinY(usable) + self.verticalFraction * MAX(0, usable.size.height - buttonHeight);
        self.toggle.frame = CGRectMake(x, y, buttonWidth, buttonHeight);
    }
    CGFloat width = MIN(340, usable.size.width);
    CGFloat height = MIN(306, usable.size.height);
    self.panel.frame = CGRectMake(CGRectGetMidX(usable) - width / 2,
                                  CGRectGetMidY(usable) - height / 2, width, height);
    CGFloat margin = 14, gap = 8;
    CGFloat column = MAX(0, (width - margin * 2 - gap) / 2);
    CGFloat rowHeight = MIN(42, MAX(28, (height - 128) / 3));
    [[self.panel viewWithTag:1001] setFrame:CGRectMake(margin, 14, width - 2 * margin, 24)];
    CGFloat top = height - margin - rowHeight * 3 - gap * 2;
    self.status.frame = CGRectMake(margin, 44, width - margin * 2, MAX(30, top - 50));
    NSArray<UIButton *> *left = @[self.startButton, self.markerButton, self.exportButton];
    NSArray<UIView *> *right = @[self.snapshotButton, self.stopButton, [self.panel viewWithTag:1002]];
    for (NSUInteger i = 0; i < 3; ++i) {
        CGFloat y = top + i * (rowHeight + gap);
        left[i].frame = CGRectMake(margin, y, column, rowHeight);
        right[i].frame = CGRectMake(margin + column + gap, y, column, rowHeight);
    }
}
- (void)togglePanel:(UIButton *)sender {
    if (self.presentedViewController) return;
    self.panel.hidden = !self.panel.hidden;
    [self refreshStatus];
}
- (void)dragToggle:(UIPanGestureRecognizer *)gesture {
    CGRect usable = [self usableBounds];
    if (gesture.state == UIGestureRecognizerStateBegan) self.dragging = YES;
    CGPoint delta = [gesture translationInView:self.view];
    CGPoint center = self.toggle.center;
    center.x += delta.x; center.y += delta.y;
    center.x = MIN(MAX(center.x, CGRectGetMinX(usable) + 28), CGRectGetMaxX(usable) - 28);
    center.y = MIN(MAX(center.y, CGRectGetMinY(usable) + 22), CGRectGetMaxY(usable) - 22);
    self.toggle.center = center;
    [gesture setTranslation:CGPointZero inView:self.view];
    if (gesture.state == UIGestureRecognizerStateEnded || gesture.state == UIGestureRecognizerStateCancelled || gesture.state == UIGestureRecognizerStateFailed) {
        self.dockRight = center.x >= CGRectGetMidX(usable);
        self.verticalFraction = MAX(0, MIN(1, (center.y - 22 - CGRectGetMinY(usable)) / MAX(1, usable.size.height - 44)));
        self.dragging = NO;
        [self.view setNeedsLayout];
    }
}
- (void)startPressed:(UIButton *)sender { [[SDKCaptureManager shared] startCapture]; [self refreshStatus]; }
- (void)snapshotPressed:(UIButton *)sender { [[SDKCaptureManager shared] captureManualSnapshot]; [self refreshStatus]; }
- (void)markerPressed:(UIButton *)sender { [[SDKCaptureManager shared] recordMarker]; [self refreshStatus]; }
- (void)stopPressed:(UIButton *)sender { [[SDKCaptureManager shared] stopCapture]; [self refreshStatus]; }
- (void)exportPressed:(UIButton *)sender {
    [[SDKCaptureManager shared] exportCaptureFromViewController:self sourceView:sender];
    [self refreshStatus];
}
- (void)refreshStatus {
    if (!self.isViewLoaded) return;
    SDKCaptureManager *manager = [SDKCaptureManager shared];
    self.status.text = manager.statusText;
    self.startButton.enabled = !manager.recording;
    self.stopButton.enabled = manager.recording;
    self.snapshotButton.enabled = manager.recording;
    self.markerButton.enabled = manager.recording;
    for (UIButton *button in @[self.startButton, self.stopButton, self.snapshotButton, self.markerButton])
        button.alpha = button.enabled ? 1 : 0.42;
    [self.toggle setTitle:manager.recording ? @"REC" : @"SDK" forState:UIControlStateNormal];
    self.toggle.backgroundColor = manager.recording ? [UIColor colorWithRed:0.75 green:0.16 blue:0.19 alpha:0.97] : [UIColor colorWithRed:0.10 green:0.40 blue:0.72 alpha:0.96];
}
- (void)setFeedbackActive:(BOOL)active {
    if (!active) { [self.statusTimer invalidate]; self.statusTimer = nil; return; }
    [self refreshStatus];
    if (self.statusTimer) return;
    __weak typeof(self) weakSelf = self;
    self.statusTimer = [NSTimer timerWithTimeInterval:0.5 repeats:YES block:^(NSTimer *timer) { [weakSelf refreshStatus]; }];
    [NSRunLoop.mainRunLoop addTimer:self.statusTimer forMode:NSRunLoopCommonModes];
}
- (BOOL)shouldAutorotate { return YES; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }
- (BOOL)prefersStatusBarHidden { return NO; }
- (void)dealloc { [_statusTimer invalidate]; }
@end

@interface SDKCaptureOverlay ()
@property(nonatomic, strong) SDKCaptureWindow *window;
@property(nonatomic, strong) SDKCapturePanelController *controller;
@property(nonatomic, strong) NSMutableArray<id> *keyboardTokens;
@property(nonatomic) BOOL keyboardVisible;
@end

@implementation SDKCaptureOverlay
- (instancetype)init {
    if ((self = [super init])) {
        _keyboardTokens = [NSMutableArray array];
        __weak typeof(self) weakSelf = self;
        id show = [NSNotificationCenter.defaultCenter addObserverForName:UIKeyboardWillShowNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note) {
            weakSelf.keyboardVisible = YES; [weakSelf refreshAttachment];
        }];
        id hide = [NSNotificationCenter.defaultCenter addObserverForName:UIKeyboardWillHideNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note) {
            weakSelf.keyboardVisible = NO; [weakSelf refreshAttachment];
        }];
        [_keyboardTokens addObjectsFromArray:@[show, hide]];
    }
    return self;
}
- (UIWindow *)hostWindow {
    UIWindow *candidate = nil;
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if (![scene isKindOfClass:UIWindowScene.class] || scene.activationState != UISceneActivationStateForegroundActive) continue;
        for (UIWindow *window in ((UIWindowScene *)scene).windows) {
            if (window.hidden || window.alpha <= 0 || [window isKindOfClass:SDKCaptureWindow.class]) continue;
            NSString *name = NSStringFromClass(window.class);
            if ([name hasPrefix:@"SDK"] || [name containsString:@"Keyboard"] || [name containsString:@"TextEffects"]) continue;
            if (window.isKeyWindow) return window;
            if (!candidate) candidate = window;
        }
    }
    if (candidate) return candidate;
    for (UIWindow *window in UIApplication.sharedApplication.windows) {
        if (window.windowScene || window.hidden || window.alpha <= 0 || [window isKindOfClass:SDKCaptureWindow.class]) continue;
        NSString *name = NSStringFromClass(window.class);
        if ([name hasPrefix:@"SDK"] || [name containsString:@"Keyboard"] || [name containsString:@"TextEffects"]) continue;
        if (window.isKeyWindow) return window;
        if (!candidate) candidate = window;
    }
    return candidate;
}
- (void)refreshAttachment {
    if (!NSThread.isMainThread) {
        __weak typeof(self) weakSelf = self;
        dispatch_async(dispatch_get_main_queue(), ^{ [weakSelf refreshAttachment]; });
        return;
    }
    BOOL active = UIApplication.sharedApplication.applicationState == UIApplicationStateActive;
    UIWindow *host = active ? [self hostWindow] : nil;
    if (!active || !host) { self.window.hidden = YES; [self.controller setFeedbackActive:NO]; return; }
    if (self.keyboardVisible && !self.controller.presentedViewController) {
        self.window.hidden = YES; [self.controller setFeedbackActive:NO]; return;
    }
    if (!self.window || self.window.windowScene != host.windowScene) {
        [self.controller setFeedbackActive:NO]; self.window.hidden = YES;
        self.controller = [[SDKCapturePanelController alloc] init];
        self.window = host.windowScene ? [[SDKCaptureWindow alloc] initWithWindowScene:host.windowScene] : [[SDKCaptureWindow alloc] initWithFrame:host.screen.bounds];
        self.window.windowLevel = UIWindowLevelStatusBar + 2;
        self.window.backgroundColor = UIColor.clearColor;
        self.window.rootViewController = self.controller;
        self.window.accessibilityViewIsModal = NO;
    }
    if (!host.windowScene) self.window.frame = host.screen.bounds;
    self.window.hidden = NO;
    // Never call makeKeyWindow / makeKeyAndVisible: the original app owns focus.
    [self.controller setFeedbackActive:YES];
}
- (void)dealloc {
    for (id token in _keyboardTokens) [NSNotificationCenter.defaultCenter removeObserver:token];
    [_controller setFeedbackActive:NO];
}
@end
